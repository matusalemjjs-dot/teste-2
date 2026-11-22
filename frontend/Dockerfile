# Frontend (Next.js). As variáveis NEXT_PUBLIC_* são embutidas no build.
FROM node:22.21-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
ARG NEXT_PUBLIC_API_URL NEXT_PUBLIC_SUPABASE_URL NEXT_PUBLIC_SUPABASE_ANON_KEY NEXT_PUBLIC_STORAGE_ORIGIN \
    NEXT_PUBLIC_BETA_MODE
ENV NEXT_TELEMETRY_DISABLED=1
RUN npm run build

FROM node:22.21-alpine
WORKDIR /app
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1
COPY --from=build --chown=node:node /app ./
USER node
EXPOSE 3000
CMD ["npx", "next", "start", "-p", "3000", "-H", "0.0.0.0"]
