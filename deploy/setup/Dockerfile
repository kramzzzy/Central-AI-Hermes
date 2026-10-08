FROM node:24-bookworm-slim
WORKDIR /app
RUN npm install --omit=dev --no-audit --no-fund pg@8.23.0
COPY scripts/first-run* scripts/runtime-config.mjs ./scripts/
USER node
EXPOSE 3000
CMD ["node", "scripts/first-run-server.mjs"]
