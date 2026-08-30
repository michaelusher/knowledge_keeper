FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
# Install with both cloud extras; trim to [azure] or [aws] for smaller images
RUN pip install --no-cache-dir ".[azure,aws,anthropic]"
COPY config.example.yaml ./config.yaml
ENV KK_DATA_DIR=/data
VOLUME /data
EXPOSE 8000
CMD ["kk", "serve", "--host", "0.0.0.0", "--port", "8000"]
