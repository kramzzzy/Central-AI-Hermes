FROM nousresearch/hermes-agent:v2026.9.24@sha256:fca358f12efd65bfaaca05884166f15c0e2788375ca30d77061ac1ebc96452b7
USER root
COPY scripts/first_run_auth.py /setup-code/first_run_auth.py
ENTRYPOINT ["/opt/hermes/.venv/bin/python", "/setup-code/first_run_auth.py"]
