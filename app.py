import os, io, csv, time, zipfile, re, requests
from flask import Flask, render_template, request, send_file

app = Flask(__name__)

# Внутри Railway проекта SearXNG доступен по имени сервиса
SEARXNG = os.environ.get("SEARXNG_URL", "http://searxng:8080")
JINA = "https://r.jina.ai/http://"

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/gather", methods=["POST"])
def gather():
    raw_queries = request.form.get("queries", "").strip()
    corpus_name = request.form.get("corpus_name", "corpus").strip() or "corpus"
    per_query = min(int(request.form.get("per_query", 5)), 10)  # макс 10 за раз

    if not raw_queries:
        return "Введите хотя бы один запрос", 400

    queries = [q.strip() for q in raw_queries.splitlines() if q.strip()]
    
    # Ограничение: не более 4 запросов за раз, чтобы не упереться в таймаут Railway
    if len(queries) > 4:
        queries = queries[:4]

    zip_buffer = io.BytesIO()
    manifest = []

    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for q in queries:
            # 1. Запрашиваем ссылки у SearXNG
            try:
                r = requests.get(
                    f"{SEARXNG}/search",
                    params={"q": q, "format": "json", "language": "ru-RU", "time_range": "year"},
                    timeout=15
                )
                results = r.json().get("results", [])[:per_query]
            except Exception as e:
                results = []

            for item in results:
                url = item.get("url")
                title = item.get("title", "untitled")[:90]
                if not url:
                    continue

                # 2. Чистим через Jina AI Reader
                try:
                    c = requests.get(f"{JINA}{url}", timeout=20)
                    c.raise_for_status()
                    text = c.text
                    status = "ok"
                except Exception as e:
                    text = f"<!-- Ошибка загрузки: {e} -->"
                    status = "error"

                # 3. Формируем .md
                safe_name = re.sub(r'[^\w]', '_', url[:70]) + ".md"
                content = f"<!-- TITLE: {title} -->\n<!-- SOURCE: {url} -->\n<!-- QUERY: {q} -->\n\n{text}"
                zf.writestr(safe_name, content.encode("utf-8"))

                manifest.append({"query": q, "title": title, "url": url, "status": status, "chars": len(text)})
                time.sleep(1)  # не ддосим

        # 4. Пишем manifest внутрь ZIP
        m = io.StringIO()
        w = csv.DictWriter(m, fieldnames=["query", "title", "url", "status", "chars"])
        w.writeheader()
        w.writerows(manifest)
        zf.writestr("manifest.csv", m.getvalue().encode("utf-8"))

    zip_buffer.seek(0)
    return send_file(
        zip_buffer,
        mimetype="application/zip",
        as_attachment=True,
        download_name=f"{corpus_name}.zip"
    )

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
