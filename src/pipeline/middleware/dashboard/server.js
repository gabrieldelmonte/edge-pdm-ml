const express = require("express");
const path = require("path");
const { createProxyMiddleware } = require("http-proxy-middleware");

const app = express();
const port = Number(process.env.PORT || 3000);
const middlewareUrl = process.env.MIDDLEWARE_URL || "http://middleware:8080";

app.use(
  "/api",
  createProxyMiddleware({
    target: middlewareUrl,
    changeOrigin: true,
    ws: false,
    logLevel: "warn",
    pathRewrite: (path) => `/api${path}`,
  })
);

app.use(express.static(path.join(__dirname, "public")));

app.get("*", (_req, res) => {
  res.sendFile(path.join(__dirname, "public", "index.html"));
});

app.listen(port, () => {
  console.log(`[dashboard] Listening on http://0.0.0.0:${port}`);
  console.log(`[dashboard] Proxying /api requests to ${middlewareUrl}`);
});
