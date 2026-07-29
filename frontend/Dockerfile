# The versioned dist directory is generated and verified before commit by
# api:check, frontend tests, TypeScript/Vite build, and the bundle budget.
# Production image assembly is therefore deterministic and does not require
# registry credentials or mutable npm availability.
FROM nginx:1.27-alpine
COPY deploy/nginx.conf /etc/nginx/conf.d/default.conf
COPY frontend/dist/ /usr/share/nginx/html/
EXPOSE 80
