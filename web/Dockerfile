# Component-test image for the dashboard. The phase proof runs
#   docker compose -f docker-compose.test.yml run --rm web npm run test:ui
# under jsdom with a mocked transport, so this image needs no backend topology.
FROM node:20-alpine
WORKDIR /app

# Install deps first so the layer caches across source edits. There is no
# committed lockfile (framework-light scaffold), so use `npm install`.
COPY package.json ./
RUN npm install

COPY . .

CMD ["npm", "run", "test:ui"]
