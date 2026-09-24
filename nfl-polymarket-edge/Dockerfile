FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt pyproject.toml ./
COPY nfl_edge ./nfl_edge
RUN pip install --no-cache-dir -r requirements.txt && pip install --no-cache-dir -e .
ENV NFL_EDGE_DATA_DIR=/data
VOLUME ["/data"]
ENTRYPOINT ["python", "-m", "nfl_edge.cli"]
CMD ["--help"]
