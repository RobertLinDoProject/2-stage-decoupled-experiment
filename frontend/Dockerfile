FROM node:22-alpine

WORKDIR /app

COPY package.json pnpm-workspace.yaml pnpm-lock.yaml ./
RUN corepack enable && corepack prepare pnpm@11.9.0 --activate && pnpm install --frozen-lockfile

COPY index.html ./
COPY public ./public
COPY eslint.config.js tsconfig.json tsconfig.node.json vite.config.ts ./
COPY src ./src

EXPOSE 5173

CMD ["pnpm", "dev"]
