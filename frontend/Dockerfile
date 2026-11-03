# Development image for the chat window (Vite dev server on :5173). Runs as the non-root `node` user.
FROM node:24.22.2-slim
WORKDIR /app
COPY package.json package-lock.json ./
# npm is needed only to install; removed after, since its bundled undici has fixable HIGH CVEs that no
# npm release has picked up yet (Trivy, ADR 0014). The dev server runs Vite directly.
RUN npm ci && rm -rf /usr/local/lib/node_modules/npm /usr/local/bin/npm /usr/local/bin/npx /root/.npm
COPY . .
RUN chown -R node:node /app
USER node
EXPOSE 5173
CMD ["node_modules/.bin/vite", "--host", "0.0.0.0"]
