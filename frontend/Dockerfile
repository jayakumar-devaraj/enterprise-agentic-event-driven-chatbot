# Development image for the chat window (Vite dev server on :5173). Runs as the non-root `node` user.
FROM node:24.22.0-slim
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN chown -R node:node /app
USER node
EXPOSE 5173
CMD ["npm", "run", "dev"]
