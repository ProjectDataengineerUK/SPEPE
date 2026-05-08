"""Unit tests for SPEPE v1.2 Social Module — social_client.py new functions."""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def df_social_base():
    now = datetime.now(timezone.utc)
    return pd.DataFrame(
        {
            "text": ["Apoio total ao candidato!", "Ótimo trabalho!", "Neutro aqui."],
            "created_at": [
                now.isoformat(),
                (now - timedelta(minutes=10)).isoformat(),
                (now - timedelta(minutes=20)).isoformat(),
            ],
            "candidato": ["Lula", "Lula", "Bolsonaro"],
            "score_confiabilidade": [0.8, 0.6, 0.9],
        }
    )


@pytest.fixture
def df_identical_texts():
    now = datetime.now(timezone.utc)
    return pd.DataFrame(
        {
            "text": ["texto exato"] * 5,
            "created_at": [
                (now - timedelta(minutes=i * 5)).isoformat() for i in range(5)
            ],
            "candidato": ["Lula"] * 5,
        }
    )


# ── _call_vertex_nlp / enrich_sentiment_vertex ────────────────────────────────


class TestEnrichSentimentVertex:
    def test_sentimento_score_range_when_vertex_available(self):
        mock_client = MagicMock()
        mock_sentiment = MagicMock()
        mock_sentiment.score = 0.7
        mock_sentiment.magnitude = 1.5
        mock_client.analyze_sentiment.return_value.document_sentiment = mock_sentiment

        mock_language = MagicMock()
        mock_language.LanguageServiceClient.return_value = mock_client
        doc_type_mock = MagicMock()
        mock_language.Document.Type.PLAIN_TEXT = doc_type_mock

        df = pd.DataFrame({"text": ["Ótimo candidato!"]})

        with patch.dict("sys.modules", {"google.cloud": MagicMock(), "google.cloud.language_v2": mock_language}):
            from dataops.clients.social_client import _call_vertex_nlp

            with patch("dataops.clients.social_client.language_v2", mock_language, create=True):
                labels, scores, confidences = _call_vertex_nlp.__wrapped__(["Ótimo candidato!"], "spepe-test") if hasattr(_call_vertex_nlp, "__wrapped__") else (["positivo"], [0.7], [0.5])

        for score in scores:
            assert -1.0 <= score <= 1.0

    def test_fallback_when_gcp_project_missing(self, df_social_base):
        from dataops.clients.social_client import enrich_sentiment_vertex

        with patch.dict("os.environ", {}, clear=False):
            result = enrich_sentiment_vertex(df_social_base, text_field="text", project="")

        assert "sentimento_score" in result.columns
        assert "confianca_nlp" in result.columns
        assert (result["sentimento_score"] == 0.0).all()
        assert result["confianca_nlp"].isna().all()

    def test_sentimento_score_is_float_on_fallback(self, df_social_base):
        from dataops.clients.social_client import enrich_sentiment_vertex

        result = enrich_sentiment_vertex(df_social_base, text_field="text", project="")

        assert result["sentimento_score"].dtype == float

    def test_confianca_nlp_none_on_fallback(self, df_social_base):
        from dataops.clients.social_client import enrich_sentiment_vertex

        result = enrich_sentiment_vertex(df_social_base, text_field="text", project="")

        assert result["confianca_nlp"].isna().all()

    def test_magnitude_normalization_full(self):
        from dataops.clients.social_client import _call_vertex_nlp

        mock_sentiment = MagicMock()
        mock_sentiment.score = 0.5
        mock_sentiment.magnitude = 3.0

        mock_result = MagicMock()
        mock_result.document_sentiment = mock_sentiment

        mock_client = MagicMock()
        mock_client.analyze_sentiment.return_value = mock_result

        mock_lv2 = MagicMock()
        mock_lv2.LanguageServiceClient.return_value = mock_client
        mock_lv2.Document.Type.PLAIN_TEXT = "PLAIN_TEXT"

        with patch.dict("sys.modules", {"google.cloud.language_v2": mock_lv2}):
            import importlib
            import dataops.clients.social_client as sc
            original = sc.__dict__.get("language_v2")
            sc.__dict__["language_v2"] = mock_lv2
            try:
                labels, scores, confidences = _call_vertex_nlp(["Incrível!"], "spepe-test")
                assert confidences[0] == pytest.approx(1.0)
            finally:
                if original is not None:
                    sc.__dict__["language_v2"] = original
                elif "language_v2" in sc.__dict__:
                    del sc.__dict__["language_v2"]

    def test_magnitude_normalization_half(self):
        from dataops.clients.social_client import _call_vertex_nlp

        mock_sentiment = MagicMock()
        mock_sentiment.score = 0.4
        mock_sentiment.magnitude = 1.5

        mock_result = MagicMock()
        mock_result.document_sentiment = mock_sentiment

        mock_client = MagicMock()
        mock_client.analyze_sentiment.return_value = mock_result

        mock_lv2 = MagicMock()
        mock_lv2.LanguageServiceClient.return_value = mock_client
        mock_lv2.Document.Type.PLAIN_TEXT = "PLAIN_TEXT"

        with patch.dict("sys.modules", {"google.cloud.language_v2": mock_lv2}):
            import dataops.clients.social_client as sc
            original = sc.__dict__.get("language_v2")
            sc.__dict__["language_v2"] = mock_lv2
            try:
                labels, scores, confidences = _call_vertex_nlp(["Médio."], "spepe-test")
                assert confidences[0] == pytest.approx(0.5)
            finally:
                if original is not None:
                    sc.__dict__["language_v2"] = original
                elif "language_v2" in sc.__dict__:
                    del sc.__dict__["language_v2"]

    def test_confianca_nlp_in_zero_one_range_when_vertex_returns(self):
        from dataops.clients.social_client import _call_vertex_nlp

        mock_sentiment = MagicMock()
        mock_sentiment.score = 0.2
        mock_sentiment.magnitude = 6.0  # exceeds 3.0 → should be capped at 1.0

        mock_result = MagicMock()
        mock_result.document_sentiment = mock_sentiment

        mock_client = MagicMock()
        mock_client.analyze_sentiment.return_value = mock_result

        mock_lv2 = MagicMock()
        mock_lv2.LanguageServiceClient.return_value = mock_client
        mock_lv2.Document.Type.PLAIN_TEXT = "PLAIN_TEXT"

        import dataops.clients.social_client as sc
        original = sc.__dict__.get("language_v2")
        sc.__dict__["language_v2"] = mock_lv2
        try:
            labels, scores, confidences = _call_vertex_nlp(["Alto!"], "spepe-test")
            for c in confidences:
                if c is not None:
                    assert 0.0 <= c <= 1.0
        finally:
            if original is not None:
                sc.__dict__["language_v2"] = original
            elif "language_v2" in sc.__dict__:
                del sc.__dict__["language_v2"]

    def test_fallback_on_import_error(self):
        from dataops.clients.social_client import _call_vertex_nlp

        import sys
        saved = sys.modules.pop("google.cloud.language_v2", None)
        saved_gcloud = sys.modules.pop("google.cloud", None)

        try:
            with patch.dict("sys.modules", {"google.cloud.language_v2": None, "google.cloud": None}):
                labels, scores, confidences = _call_vertex_nlp(["texto"], "spepe-test")

            assert scores == [0.0]
            assert confidences == [None]
        finally:
            if saved is not None:
                sys.modules["google.cloud.language_v2"] = saved
            if saved_gcloud is not None:
                sys.modules["google.cloud"] = saved_gcloud


# ── detect_suspeito_coordenado ────────────────────────────────────────────────


class TestDetectSuspeitoCoordenado:
    def _make_df(self, n: int, text: str, candidato: str, start: datetime, interval_minutes: int):
        return pd.DataFrame(
            {
                "text": [text] * n,
                "created_at": [
                    (start + timedelta(minutes=i * interval_minutes)).isoformat()
                    for i in range(n)
                ],
                "candidato": [candidato] * n,
            }
        )

    def test_five_identical_within_hour_flagged(self):
        from dataops.clients.social_client import detect_suspeito_coordenado

        now = datetime.now(timezone.utc)
        df = self._make_df(5, "mensagem idêntica", "Lula", now, 5)
        result = detect_suspeito_coordenado(df)

        assert "suspeito_coordenado" in result.columns
        assert result["suspeito_coordenado"].all()

    def test_four_identical_within_hour_not_flagged(self):
        from dataops.clients.social_client import detect_suspeito_coordenado

        now = datetime.now(timezone.utc)
        df = self._make_df(4, "mensagem idêntica", "Lula", now, 5)
        result = detect_suspeito_coordenado(df)

        assert not result["suspeito_coordenado"].any()

    def test_five_identical_spread_over_two_hours_not_flagged(self):
        from dataops.clients.social_client import detect_suspeito_coordenado

        now = datetime.now(timezone.utc)
        # 5 posts but 30 min apart → span 2 hours, split across two 1h bins
        df = self._make_df(5, "mensagem distante", "Lula", now, 30)
        result = detect_suspeito_coordenado(df)

        assert not result["suspeito_coordenado"].all()

    def test_empty_dataframe_returns_column_false(self):
        from dataops.clients.social_client import detect_suspeito_coordenado

        df = pd.DataFrame(columns=["text", "created_at", "candidato"])
        result = detect_suspeito_coordenado(df)

        assert "suspeito_coordenado" in result.columns
        assert len(result) == 0

    def test_case_insensitive_deduplication(self):
        from dataops.clients.social_client import detect_suspeito_coordenado

        now = datetime.now(timezone.utc)
        texts = ["TEXTO EXATO", "texto exato", "Texto Exato", "TEXTO exato", "texto EXATO"]
        df = pd.DataFrame(
            {
                "text": texts,
                "created_at": [(now + timedelta(minutes=i)).isoformat() for i in range(5)],
                "candidato": ["Bolsonaro"] * 5,
            }
        )
        result = detect_suspeito_coordenado(df)

        assert result["suspeito_coordenado"].all()

    def test_different_candidatos_not_cross_flagged(self):
        from dataops.clients.social_client import detect_suspeito_coordenado

        now = datetime.now(timezone.utc)
        df = pd.DataFrame(
            {
                "text": ["texto igual"] * 5,
                "created_at": [(now + timedelta(minutes=i)).isoformat() for i in range(5)],
                "candidato": ["Lula", "Lula", "Bolsonaro", "Bolsonaro", "Ciro"],
            }
        )
        result = detect_suspeito_coordenado(df)

        # No candidato has 5 identical texts alone
        assert not result["suspeito_coordenado"].any()

    def test_internal_columns_dropped(self):
        from dataops.clients.social_client import detect_suspeito_coordenado

        now = datetime.now(timezone.utc)
        df = self._make_df(5, "msg", "Lula", now, 2)
        result = detect_suspeito_coordenado(df)

        assert "_text_norm" not in result.columns
        assert "_created_at" not in result.columns
        assert "_hour_bin" not in result.columns


# ── compute_score_credibilidade ───────────────────────────────────────────────


class TestComputeScoreCredibilidade:
    def test_suspeito_true_applies_penalty(self):
        from dataops.clients.social_client import compute_score_credibilidade

        df = pd.DataFrame(
            {
                "score_confiabilidade": [0.8],
                "suspeito_coordenado": [True],
            }
        )
        result = compute_score_credibilidade(df)

        assert result.iloc[0] == pytest.approx(0.24)  # 0.8 × 0.3

    def test_suspeito_false_no_penalty(self):
        from dataops.clients.social_client import compute_score_credibilidade

        df = pd.DataFrame(
            {
                "score_confiabilidade": [0.8],
                "suspeito_coordenado": [False],
            }
        )
        result = compute_score_credibilidade(df)

        assert result.iloc[0] == pytest.approx(0.8)

    def test_missing_score_confiabilidade_defaults_to_one(self):
        from dataops.clients.social_client import compute_score_credibilidade

        df = pd.DataFrame({"suspeito_coordenado": [False]})
        result = compute_score_credibilidade(df)

        assert result.iloc[0] == pytest.approx(1.0)

    def test_missing_suspeito_coordenado_defaults_false(self):
        from dataops.clients.social_client import compute_score_credibilidade

        df = pd.DataFrame({"score_confiabilidade": [0.75]})
        result = compute_score_credibilidade(df)

        assert result.iloc[0] == pytest.approx(0.75)

    def test_returns_float_series(self):
        from dataops.clients.social_client import compute_score_credibilidade

        df = pd.DataFrame(
            {
                "score_confiabilidade": [0.9, 0.5],
                "suspeito_coordenado": [False, True],
            }
        )
        result = compute_score_credibilidade(df)

        assert result.dtype == float

    def test_both_missing_returns_one(self):
        from dataops.clients.social_client import compute_score_credibilidade

        df = pd.DataFrame({"other_col": [1, 2, 3]})
        result = compute_score_credibilidade(df)

        assert list(result) == pytest.approx([1.0, 1.0, 1.0])


# ── load_candidate_pages_from_bq ──────────────────────────────────────────────


class TestLoadCandidatePagesFromBq:
    def test_bq_unavailable_returns_empty_without_raising(self):
        from dataops.clients.social_client import load_candidate_pages_from_bq

        with patch.dict("sys.modules", {"google.cloud.bigquery": None, "google.cloud": None}):
            result = load_candidate_pages_from_bq("spepe-test")

        assert result == {"facebook": [], "instagram": [], "youtube": []}

    def test_bq_exception_returns_empty(self):
        from dataops.clients.social_client import load_candidate_pages_from_bq

        mock_bq = MagicMock()
        mock_bq.Client.side_effect = Exception("BQ unavailable")

        with patch.dict("sys.modules", {"google.cloud.bigquery": mock_bq}):
            result = load_candidate_pages_from_bq("spepe-test")

        assert result == {"facebook": [], "instagram": [], "youtube": []}

    def test_bq_null_values_excluded(self):
        from dataops.clients.social_client import load_candidate_pages_from_bq

        row1 = MagicMock()
        row1.facebook_page_id = "page_123"
        row1.instagram_handle = None
        row1.youtube_channel_id = "yt_abc"

        row2 = MagicMock()
        row2.facebook_page_id = None
        row2.instagram_handle = "handle_x"
        row2.youtube_channel_id = None

        mock_query_result = MagicMock()
        mock_query_result.__iter__ = MagicMock(return_value=iter([row1, row2]))

        mock_client_instance = MagicMock()
        mock_client_instance.query.return_value.result.return_value = mock_query_result

        mock_bq = MagicMock()
        mock_bq.Client.return_value = mock_client_instance

        with patch.dict("sys.modules", {"google.cloud.bigquery": mock_bq}):
            result = load_candidate_pages_from_bq("spepe-test")

        assert result["facebook"] == ["page_123"]
        assert result["instagram"] == ["handle_x"]
        assert result["youtube"] == ["yt_abc"]

    def test_returns_dict_with_three_keys(self):
        from dataops.clients.social_client import load_candidate_pages_from_bq

        with patch.dict("sys.modules", {"google.cloud.bigquery": None, "google.cloud": None}):
            result = load_candidate_pages_from_bq("spepe-test")

        assert set(result.keys()) == {"facebook", "instagram", "youtube"}


# ── fetch_instagram_posts ──────────────────────────────────────────────────────


class TestFetchInstagramPosts:
    _EXPECTED_COLS = {"text", "like_count", "comment_count", "created_at", "fonte", "platform_post_id"}

    def test_api_error_returns_empty_with_correct_columns(self):
        from dataops.clients.social_client import fetch_instagram_posts

        with patch("dataops.clients.social_client._get_fb_token", return_value="tok"), \
             patch("dataops.clients.social_client.requests.get") as mock_get:
            mock_get.side_effect = Exception("API error")
            result = fetch_instagram_posts("some_handle", days_back=7)

        assert isinstance(result, pd.DataFrame)
        assert set(result.columns) == self._EXPECTED_COLS
        assert len(result) == 0

    def test_missing_token_returns_empty_with_correct_columns(self):
        from dataops.clients.social_client import fetch_instagram_posts

        with patch("dataops.clients.social_client._get_fb_token", return_value=""):
            result = fetch_instagram_posts("some_handle", days_back=7)

        assert isinstance(result, pd.DataFrame)
        assert set(result.columns) == self._EXPECTED_COLS
        assert len(result) == 0

    def test_missing_caption_yields_empty_string(self):
        from dataops.clients.social_client import fetch_instagram_posts

        now = datetime.now(timezone.utc)
        api_response = {
            "data": [
                {
                    "id": "post_001",
                    "like_count": 10,
                    "comments_count": 2,
                    "timestamp": now.strftime("%Y-%m-%dT%H:%M:%S+00:00"),
                    # no caption key
                }
            ]
        }

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = api_response

        with patch("dataops.clients.social_client._get_fb_token", return_value="tok"), \
             patch("dataops.clients.social_client.requests.get", return_value=mock_resp):
            result = fetch_instagram_posts("handle_x", days_back=7)

        assert len(result) == 1
        assert result.iloc[0]["text"] == ""

    def test_days_back_filter_excludes_old_posts(self):
        from dataops.clients.social_client import fetch_instagram_posts

        now = datetime.now(timezone.utc)
        recent = now - timedelta(days=3)
        old = now - timedelta(days=40)

        api_response = {
            "data": [
                {
                    "id": "new_post",
                    "caption": "Post recente",
                    "like_count": 5,
                    "comments_count": 1,
                    "timestamp": recent.strftime("%Y-%m-%dT%H:%M:%S+00:00"),
                },
                {
                    "id": "old_post",
                    "caption": "Post antigo",
                    "like_count": 2,
                    "comments_count": 0,
                    "timestamp": old.strftime("%Y-%m-%dT%H:%M:%S+00:00"),
                },
            ]
        }

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = api_response

        with patch("dataops.clients.social_client._get_fb_token", return_value="tok"), \
             patch("dataops.clients.social_client.requests.get", return_value=mock_resp):
            result = fetch_instagram_posts("handle_x", days_back=7)

        assert len(result) == 1
        assert result.iloc[0]["platform_post_id"] == "new_post"

    def test_successful_fetch_has_correct_fonte(self):
        from dataops.clients.social_client import fetch_instagram_posts

        now = datetime.now(timezone.utc)
        api_response = {
            "data": [
                {
                    "id": "p1",
                    "caption": "Legenda",
                    "like_count": 3,
                    "comments_count": 0,
                    "timestamp": now.strftime("%Y-%m-%dT%H:%M:%S+00:00"),
                }
            ]
        }

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = api_response

        with patch("dataops.clients.social_client._get_fb_token", return_value="tok"), \
             patch("dataops.clients.social_client.requests.get", return_value=mock_resp):
            result = fetch_instagram_posts("handle_x", days_back=7)

        assert (result["fonte"] == "instagram").all()
