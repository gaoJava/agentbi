ARG SUPERSET_BASE_IMAGE=superset-main-superset:latest
FROM ${SUPERSET_BASE_IMAGE}

USER root
COPY integrations/superset-extension /app/extensions/agentbi-insight-pilot
COPY deploy/offline/superset/superset_config.py /app/pythonpath/superset_config.py
COPY deploy/superset/superset_config_docker.py /app/docker/pythonpath_dev/superset_config_docker.py

LABEL org.opencontainers.image.title="InsightPilot Superset" \
      org.opencontainers.image.description="Apache Superset with the InsightPilot AgentBI extension"

CMD ["/usr/bin/run-server.sh"]
