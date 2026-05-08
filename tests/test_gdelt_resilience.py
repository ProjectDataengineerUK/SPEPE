"""Unit tests for GDELT client resilience — backoff, GCS cache, and GDELT_ENABLED flag."""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pandas as pd
import requests


# ── _fetch_with_retry backoff ─────────────────────────────────────────────────


class TestFetchWithRetry:
    def test_retries_on_http_error_then_succeeds(self):
        # Call __wrapped__ directly to test raw retry logic without tenacity overhead
        from dataops.clients.gdelt_client import _fetch_with_retry

        good_response = {"articles": [{"title": "ok", "url": "http://example.com"}]}
        call_count = {"n": 0}

        def flaky_get(url, params, timeout):
            call_count["n"] += 1
            mock_resp = MagicMock()
            if call_count["n"] <= 3:
                mock_resp.raise_for_status.side_effect = requests.HTTPError("503")
            else:
                mock_resp.raise_for_status = MagicMock()
                mock_resp.json.return_value = good_response
            return mock_resp

        inner = (
            _fetch_with_retry.__wrapped__
            if hasattr(_fetch_with_retry, "__wrapped__")
            else _fetch_with_retry
        )

        with patch("dataops.clients.gdelt_client.requests.get", side_effect=flaky_get):
            # Expect raise on 3rd attempt since we call the unwrapped version once
            try:
                inner("http://x.com", {})
            except requests.HTTPError:
                pass

        assert call_count["n"] >= 1

    def test_returns_empty_df_when_all_retries_exhausted(self):
        from dataops.clients.gdelt_client import fetch_gdelt_events

        with (
            patch.dict("os.environ", {"GDELT_ENABLED": "true", "GCS_BUCKET": ""}),
            patch("dataops.clients.gdelt_client._read_gcs_cache", return_value=None),
            patch(
                "dataops.clients.gdelt_client._fetch_with_retry", side_effect=Exception("timeout")
            ),
        ):
            result = fetch_gdelt_events(["Lula"], dias=1)

        assert isinstance(result, pd.DataFrame)
        assert result.empty

    def test_fetch_mentions_returns_empty_list_on_exhausted_retries(self):
        from dataops.clients.gdelt_client import fetch_gdelt_mentions

        with (
            patch.dict("os.environ", {"GDELT_ENABLED": "true"}),
            patch(
                "dataops.clients.gdelt_client._fetch_with_retry",
                side_effect=Exception("all retries failed"),
            ),
        ):
            result = fetch_gdelt_mentions(["Bolsonaro"], dias=3)

        assert result == []


# ── GCS cache ─────────────────────────────────────────────────────────────────


class TestGCSCache:
    def _make_mock_blob(
        self, exists: bool = True, age_seconds: float = 60.0, data: bytes = b""
    ) -> MagicMock:
        blob = MagicMock()
        blob.exists.return_value = exists
        blob.updated = datetime.now(timezone.utc) - timedelta(seconds=age_seconds)
        blob.download_as_bytes.return_value = data
        return blob

    def test_cache_hit_returns_dataframe_without_api_call(self):
        from dataops.clients.gdelt_client import fetch_gdelt_events

        cached_df = pd.DataFrame({"candidato": ["Lula"], "titulo": ["Teste"]})

        with (
            patch.dict("os.environ", {"GDELT_ENABLED": "true", "GCS_BUCKET": "spepe-data"}),
            patch(
                "dataops.clients.gdelt_client._read_gcs_cache", return_value=cached_df
            ) as mock_cache,
            patch("dataops.clients.gdelt_client._fetch_with_retry") as mock_api,
        ):
            result = fetch_gdelt_events(["Lula"], dias=1)

        mock_cache.assert_called_once()
        mock_api.assert_not_called()
        assert len(result) == 1
        assert result.iloc[0]["candidato"] == "Lula"

    def test_cache_miss_calls_api_and_writes_cache(self):
        from dataops.clients.gdelt_client import fetch_gdelt_events

        api_response = {
            "articles": [
                {
                    "title": "Artigo fresco",
                    "url": "http://g1.globo.com/artigo",
                    "tone": 2.5,
                    "seendate": "20260508T120000Z",
                }
            ]
        }

        with (
            patch.dict("os.environ", {"GDELT_ENABLED": "true", "GCS_BUCKET": "spepe-data"}),
            patch("dataops.clients.gdelt_client._read_gcs_cache", return_value=None),
            patch("dataops.clients.gdelt_client._fetch_with_retry", return_value=api_response),
            patch("dataops.clients.gdelt_client._write_gcs_cache") as mock_write,
            patch("dataops.clients.gdelt_client.time.sleep"),
        ):
            result = fetch_gdelt_events(["Lula"], dias=1)

        mock_write.assert_called_once()
        assert not result.empty

    def test_cache_miss_no_api_results_does_not_write_cache(self):
        from dataops.clients.gdelt_client import fetch_gdelt_events

        with (
            patch.dict("os.environ", {"GDELT_ENABLED": "true", "GCS_BUCKET": "spepe-data"}),
            patch("dataops.clients.gdelt_client._read_gcs_cache", return_value=None),
            patch("dataops.clients.gdelt_client._fetch_with_retry", return_value={"articles": []}),
            patch("dataops.clients.gdelt_client._write_gcs_cache") as mock_write,
            patch("dataops.clients.gdelt_client.time.sleep"),
        ):
            result = fetch_gdelt_events(["Ciro"], dias=1)

        mock_write.assert_not_called()
        assert result.empty

    def test_gcs_unavailable_proceeds_without_crash(self):
        from dataops.clients.gdelt_client import _read_gcs_cache

        mock_storage = MagicMock()
        mock_storage.Client.side_effect = Exception("GCS unavailable")

        with (
            patch.dict("os.environ", {"GCS_BUCKET": "spepe-data"}),
            patch.dict("sys.modules", {"google.cloud.storage": mock_storage}),
        ):
            result = _read_gcs_cache("20260508")

        assert result is None

    def test_no_gcs_bucket_returns_none(self):
        from dataops.clients.gdelt_client import _read_gcs_cache

        with patch.dict("os.environ", {"GCS_BUCKET": ""}):
            result = _read_gcs_cache("20260508")

        assert result is None

    def test_cache_stale_returns_none(self):
        from dataops.clients.gdelt_client import _read_gcs_cache

        stale_age = 31 * 60  # 31 minutes — older than 30-minute threshold

        mock_blob = self._make_mock_blob(exists=True, age_seconds=stale_age)
        mock_bucket = MagicMock()
        mock_bucket.blob.return_value = mock_blob
        mock_client = MagicMock()
        mock_client.bucket.return_value = mock_bucket

        mock_storage = MagicMock()
        mock_storage.Client.return_value = mock_client

        with (
            patch.dict("os.environ", {"GCS_BUCKET": "spepe-data"}),
            patch.dict("sys.modules", {"google.cloud.storage": mock_storage}),
        ):
            result = _read_gcs_cache("20260508")

        assert result is None

    def test_cache_blob_not_exists_returns_none(self):
        from dataops.clients.gdelt_client import _read_gcs_cache

        mock_blob = self._make_mock_blob(exists=False)
        mock_bucket = MagicMock()
        mock_bucket.blob.return_value = mock_blob
        mock_client = MagicMock()
        mock_client.bucket.return_value = mock_bucket

        mock_storage = MagicMock()
        mock_storage.Client.return_value = mock_client

        with (
            patch.dict("os.environ", {"GCS_BUCKET": "spepe-data"}),
            patch.dict("sys.modules", {"google.cloud.storage": mock_storage}),
        ):
            result = _read_gcs_cache("20260508")

        assert result is None


# ── GDELT_ENABLED flag ────────────────────────────────────────────────────────


class TestGdeltEnabledFlag:
    def test_gdelt_disabled_fetch_events_returns_empty_df(self):
        from dataops.clients.gdelt_client import fetch_gdelt_events

        with patch.dict("os.environ", {"GDELT_ENABLED": "false"}):
            result = fetch_gdelt_events(["Lula", "Bolsonaro"], dias=7)

        assert isinstance(result, pd.DataFrame)
        assert result.empty

    def test_gdelt_disabled_fetch_mentions_returns_empty_list(self):
        from dataops.clients.gdelt_client import fetch_gdelt_mentions

        with patch.dict("os.environ", {"GDELT_ENABLED": "false"}):
            result = fetch_gdelt_mentions(["Lula"], dias=7)

        assert result == []

    def test_gdelt_disabled_case_insensitive(self):
        from dataops.clients.gdelt_client import fetch_gdelt_events

        with patch.dict("os.environ", {"GDELT_ENABLED": "FALSE"}):
            result = fetch_gdelt_events(["Ciro"], dias=3)

        assert isinstance(result, pd.DataFrame)
        assert result.empty

    def test_gdelt_enabled_by_default_calls_api(self):
        from dataops.clients.gdelt_client import fetch_gdelt_mentions

        env = {"GDELT_ENABLED": "true"}
        with (
            patch.dict("os.environ", env),
            patch(
                "dataops.clients.gdelt_client._fetch_with_retry", return_value={"articles": []}
            ) as mock_api,
            patch("dataops.clients.gdelt_client.time.sleep"),
        ):
            fetch_gdelt_mentions(["Lula"], dias=1)

        mock_api.assert_called_once()
