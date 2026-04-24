"""Cloud Run job triggered by Eventarc (Pub/Sub drift-detected) to submit Vertex AI Pipeline."""
from __future__ import annotations

import base64
import json
import logging
import os
import sys

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)


def handle_pubsub_event(event_data: dict) -> int:
    """Parse Pub/Sub event and trigger Vertex AI Pipeline if cooldown allows."""
    project_id = os.environ.get("GCP_PROJECT_ID", "spepe-dev")

    try:
        data_b64 = event_data.get("data", "")
        payload = json.loads(base64.b64decode(data_b64).decode("utf-8"))
    except Exception as exc:
        logger.error("Failed to decode Pub/Sub payload: %s", exc)
        return 1

    feature = payload.get("feature", "unknown")
    js_score = payload.get("js_divergence", 0.0)
    logger.info("Drift event received: feature=%s  JS=%.4f", feature, js_score)

    from mlops.deployment.auto_rollback import check_cooldown
    if not check_cooldown(project_id):
        logger.info("Cooldown active — skipping retrain trigger")
        return 0

    try:
        from mlops.vertex_pipeline import compile_pipeline, submit_pipeline
        pipeline_file = "/tmp/spepe_pipeline.yaml"
        compile_pipeline(pipeline_file)
        job_name = f"spepe-retrain-drift-{feature.replace('_', '-')}"
        submit_pipeline(
            pipeline_yaml=pipeline_file,
            project_id=project_id,
            location=os.environ.get("VERTEX_LOCATION", "us-central1"),
            pipeline_root=f"gs://{os.environ.get('GCS_BUCKET', 'spepe-dev-data')}/vertex-pipelines",
            job_display_name=job_name,
        )
        logger.info("Vertex AI Pipeline submitted: %s", job_name)
        return 0
    except Exception as exc:
        logger.error("Failed to submit Vertex pipeline: %s", exc)
        return 1


def main() -> None:
    import flask
    app = flask.Flask(__name__)

    @app.route("/jobs/retrain-trigger", methods=["POST"])
    def retrain_trigger():
        envelope = flask.request.get_json(silent=True) or {}
        message = envelope.get("message", {})
        exit_code = handle_pubsub_event(message)
        return ("OK", 204) if exit_code == 0 else ("Error", 500)

    @app.route("/_health", methods=["GET"])
    def health():
        return "ok", 200

    port = int(os.environ.get("PORT", 8081))
    app.run(host="0.0.0.0", port=port)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--test":
        sample = {
            "data": base64.b64encode(
                json.dumps({"feature": "idhm_2010", "js_divergence": 0.15}).encode()
            ).decode()
        }
        sys.exit(handle_pubsub_event(sample))
    else:
        main()
