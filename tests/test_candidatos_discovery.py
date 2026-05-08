"""Unit tests for candidatos_discovery_job.py and scripts/discover_candidate_pages.py."""

from unittest.mock import MagicMock, patch

import pandas as pd
import requests


# ── search_facebook_page (discover_candidate_pages.py) ───────────────────────


class TestSearchFacebookPageScript:
    def _make_resp(self, pages: list) -> MagicMock:
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {"data": pages}
        return mock_resp

    def test_api_returns_empty_returns_none(self):
        from scripts.discover_candidate_pages import search_facebook_page

        with patch(
            "scripts.discover_candidate_pages.requests.get", return_value=self._make_resp([])
        ):
            result = search_facebook_page("Candidato X", "tok")

        assert result is None

    def test_returns_highest_scored_page_verified_wins(self):
        from scripts.discover_candidate_pages import search_facebook_page

        pages = [
            {
                "id": "unverified_001",
                "name": "Candidato Y",
                "fan_count": 100000,
                "verification_status": "not_verified",
                "about": "candidato a governador",
            },
            {
                "id": "verified_002",
                "name": "Candidato Y Oficial",
                "fan_count": 50000,
                "verification_status": "blue_verified",
                "about": "",
            },
        ]
        with patch(
            "scripts.discover_candidate_pages.requests.get", return_value=self._make_resp(pages)
        ):
            result = search_facebook_page("Candidato Y", "tok")

        assert result is not None
        assert result["id"] == "verified_002"

    def test_returns_highest_scored_page_fan_count_tiebreak(self):
        from scripts.discover_candidate_pages import search_facebook_page

        pages = [
            {
                "id": "small_page",
                "name": "Candidato Z Fan Page",
                "fan_count": 1000,
                "verification_status": "not_verified",
                "about": "",
            },
            {
                "id": "big_page",
                "name": "Candidato Z",
                "fan_count": 9000000,
                "verification_status": "not_verified",
                "about": "",
            },
        ]
        with patch(
            "scripts.discover_candidate_pages.requests.get", return_value=self._make_resp(pages)
        ):
            result = search_facebook_page("Candidato Z", "tok")

        assert result is not None
        assert result["id"] == "big_page"

    def test_requests_exception_returns_none_no_raise(self):
        from scripts.discover_candidate_pages import search_facebook_page

        with patch(
            "scripts.discover_candidate_pages.requests.get",
            side_effect=requests.ConnectionError("timeout"),
        ):
            result = search_facebook_page("Candidato Erro", "tok")

        assert result is None

    def test_http_error_returns_none(self):
        from scripts.discover_candidate_pages import search_facebook_page

        mock_resp = MagicMock()
        mock_resp.raise_for_status.side_effect = requests.HTTPError("403 Forbidden")
        with patch("scripts.discover_candidate_pages.requests.get", return_value=mock_resp):
            result = search_facebook_page("Candidato 403", "tok")

        assert result is None

    def test_about_keyword_bonus_applied(self):
        from scripts.discover_candidate_pages import search_facebook_page

        pages = [
            {
                "id": "with_about",
                "name": "Dep. Silva",
                "fan_count": 1000,
                "verification_status": "not_verified",
                "about": "deputado federal pelo partido",
            },
            {
                "id": "without_about",
                "name": "Silva Candidato",
                "fan_count": 2000,
                "verification_status": "not_verified",
                "about": "",
            },
        ]
        # fan_count score: 2000^0.3 ≈ 12, 1000^0.3 ≈ 8
        # about bonus: +20 for with_about → total 28 vs 12
        with patch(
            "scripts.discover_candidate_pages.requests.get", return_value=self._make_resp(pages)
        ):
            result = search_facebook_page("Silva", "tok")

        assert result is not None
        assert result["id"] == "with_about"


# ── search_facebook_page (candidatos_discovery_job.py) ───────────────────────


class TestSearchFacebookPageJob:
    def _make_resp(self, pages: list) -> MagicMock:
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {"data": pages}
        return mock_resp

    def test_api_returns_empty_returns_none(self):
        from dataops.jobs.candidatos_discovery_job import search_facebook_page

        with patch(
            "dataops.jobs.candidatos_discovery_job.requests.get", return_value=self._make_resp([])
        ):
            result = search_facebook_page("Candidato X", "tok")

        assert result is None

    def test_verified_page_wins_over_unverified(self):
        from dataops.jobs.candidatos_discovery_job import search_facebook_page

        pages = [
            {
                "id": "unverified_A",
                "name": "Cand A",
                "fan_count": 200000,
                "verification_status": "not_verified",
                "about": "",
            },
            {
                "id": "verified_B",
                "name": "Cand A Oficial",
                "fan_count": 10000,
                "verification_status": "blue_verified",
                "about": "",
            },
        ]
        with patch(
            "dataops.jobs.candidatos_discovery_job.requests.get",
            return_value=self._make_resp(pages),
        ):
            result = search_facebook_page("Cand A", "tok")

        assert result is not None
        assert result["id"] == "verified_B"

    def test_requests_exception_returns_none_logs_warning(self):
        from dataops.jobs.candidatos_discovery_job import search_facebook_page

        with (
            patch(
                "dataops.jobs.candidatos_discovery_job.requests.get",
                side_effect=requests.Timeout("timeout"),
            ),
            patch("dataops.jobs.candidatos_discovery_job.logger") as mock_logger,
        ):
            result = search_facebook_page("Cand Erro", "tok")

        assert result is None
        mock_logger.warning.assert_called_once()


# ── Job integration (light mock) ──────────────────────────────────────────────


class TestCandidatosDiscoveryJobIntegration:
    def _make_bq_client(self, candidatos_df: pd.DataFrame) -> MagicMock:
        mock_client = MagicMock()
        mock_client.query.return_value.to_dataframe.return_value = candidatos_df
        mock_load_job = MagicMock()
        mock_load_job.result = MagicMock()
        mock_client.load_table_from_dataframe.return_value = mock_load_job
        return mock_client

    def test_no_candidates_job_completes_without_error(self):
        from dataops.jobs.candidatos_discovery_job import main

        empty_df = pd.DataFrame(columns=["candidato", "mencoes"])
        mock_bq_client = self._make_bq_client(empty_df)

        with (
            patch.dict(
                "os.environ", {"GCP_PROJECT_ID": "spepe-test", "META_APP_TOKEN": "fake_tok"}
            ),
            patch("dataops.jobs.candidatos_discovery_job.get_secret", return_value="fake_tok"),
            patch("google.cloud.bigquery.Client", return_value=mock_bq_client),
        ):
            main()

        mock_bq_client.load_table_from_dataframe.assert_not_called()

    def test_no_candidates_zero_rows_inserted(self):
        from dataops.jobs.candidatos_discovery_job import main

        empty_df = pd.DataFrame(columns=["candidato", "mencoes"])
        mock_bq_client = self._make_bq_client(empty_df)

        with (
            patch.dict(
                "os.environ", {"GCP_PROJECT_ID": "spepe-test", "META_APP_TOKEN": "fake_tok"}
            ),
            patch("dataops.jobs.candidatos_discovery_job.get_secret", return_value="fake_tok"),
            patch("google.cloud.bigquery.Client", return_value=mock_bq_client),
        ):
            main()

        assert len(mock_bq_client.load_table_from_dataframe.call_args_list) == 0

    def test_bq_insert_called_with_correct_schema_columns(self):
        from dataops.jobs.candidatos_discovery_job import main

        candidatos_df = pd.DataFrame({"candidato": ["Lula", "Bolsonaro"], "mencoes": [10, 8]})
        mock_bq_client = self._make_bq_client(candidatos_df)

        with (
            patch.dict(
                "os.environ", {"GCP_PROJECT_ID": "spepe-test", "META_APP_TOKEN": "fake_tok"}
            ),
            patch("dataops.jobs.candidatos_discovery_job.get_secret", return_value="fake_tok"),
            patch("dataops.jobs.candidatos_discovery_job.search_facebook_page", return_value=None),
            patch("google.cloud.bigquery.Client", return_value=mock_bq_client),
        ):
            main()

        assert mock_bq_client.load_table_from_dataframe.called
        df_arg = mock_bq_client.load_table_from_dataframe.call_args[0][0]

        expected_cols = {
            "candidato_id",
            "nome_candidato",
            "facebook_page_id",
            "facebook_page_name",
            "followers_fb",
            "is_verified",
            "instagram_handle",
            "youtube_channel_id",
            "twitter_handle",
            "tiktok_handle",
            "updated_at",
        }
        assert expected_cols.issubset(set(df_arg.columns))

    def test_bq_insert_table_ref_uses_project(self):
        from dataops.jobs.candidatos_discovery_job import main

        candidatos_df = pd.DataFrame({"candidato": ["Ciro"], "mencoes": [5]})
        mock_bq_client = self._make_bq_client(candidatos_df)

        with (
            patch.dict(
                "os.environ", {"GCP_PROJECT_ID": "spepe-prod", "META_APP_TOKEN": "fake_tok"}
            ),
            patch("dataops.jobs.candidatos_discovery_job.get_secret", return_value="fake_tok"),
            patch("dataops.jobs.candidatos_discovery_job.search_facebook_page", return_value=None),
            patch("google.cloud.bigquery.Client", return_value=mock_bq_client),
        ):
            main()

        assert mock_bq_client.load_table_from_dataframe.called
        table_ref_arg = mock_bq_client.load_table_from_dataframe.call_args[0][1]
        assert "spepe-prod" in table_ref_arg
        assert "dim_candidato_social_pages" in table_ref_arg

    def test_candidate_with_no_fb_page_inserts_null_ids(self):
        from dataops.jobs.candidatos_discovery_job import main

        candidatos_df = pd.DataFrame({"candidato": ["Candidato Sem Página"], "mencoes": [4]})
        mock_bq_client = self._make_bq_client(candidatos_df)

        with (
            patch.dict(
                "os.environ", {"GCP_PROJECT_ID": "spepe-test", "META_APP_TOKEN": "fake_tok"}
            ),
            patch("dataops.jobs.candidatos_discovery_job.get_secret", return_value="fake_tok"),
            patch("dataops.jobs.candidatos_discovery_job.search_facebook_page", return_value=None),
            patch("google.cloud.bigquery.Client", return_value=mock_bq_client),
        ):
            main()

        df_arg = mock_bq_client.load_table_from_dataframe.call_args[0][0]
        row = df_arg.iloc[0]
        assert row["facebook_page_id"] is None
        assert row["facebook_page_name"] is None
        assert row["followers_fb"] == 0
        assert not row["is_verified"]
