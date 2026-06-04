import os, io, csv, time, zipfile, re, requests, sys, logging
from flask import Flask, render_template, request, send_file

app = Flask(__name__)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)

TAVILY_API_KEY = os.environ.get("TAVILY_API_KEY")
TAVILY_URL = "https://api.tavily.com/search"
JINA = "https://r.jina.ai/http://"


def call_tavily(q, per_query):
    if not TAVILY_API_KEY:
        logging.error("TAVILY_API_KEY не задан в переменных Railway")
        return [], None

    payload = {
        "api_key": TAVILY_API_KEY,
        "query": q,
        "search_depth": "basic",
        "include_answer": False,
        "max_results": per_query
    }

    try:
        logging.info(f"Запрос Tavily: {q}")
        r = requests.post(TAVILY_URL, json=payload, timeout=20)
        r.raise_for_status()
        data = r.json()
        results = []
        for res in data.get("results", []):
            results.append({
                "title": res.get("title", "untitled")[:90],
                "url": res.get("url"),
                "engine": "tavily"
            })
        logging.info(f"  → получено ссылок: {len(results)}")
        return results, "tavily"
    except Exception as e:
        logging.error(f"  → Tavily вернул ошибку: {e}")
        return [], None


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/gather", methods=["POST"])
def gather():
    raw_queries = request.form.get("queries", "").strip()
    corpus_name = request.form.get("corpus_name", "corpus").strip() or "corpus"
    try:
        per_query = min(int(request.form.get("per_query", 5)), 10)
    except ValueError:
        per_query = 5

    if not raw_queries:
        return "Введите хотя бы один запрос", 400

    queries = [q.strip() for q in raw_queries.splitlines() if q.strip()]
    queries = queries[:4]  # railway hobby может убить по таймауту, если больше

    logging.info(f"START | корпус={corpus_name} | запросы={queries} | depth={per_query}")

    zip_buffer = io.BytesIO()
    manifest = []

    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for q in queries:
            results, used_base = call_tavily(q, per_query)

            if not results:
                manifest.append({
                    "query": q,
                    "title": "NO_RESULTS",
                    "url": "N/A",
                    "status": "no_results_or_error",
                    "chars": 0,
                    "source_engine": "N/A"
                })
                continue

            for item in results:
                url = item["url"]
                title = item["title"]
                if not url:
                    continue

                # Чистим через Jina AI Reader
                try:
                    c = requests.get(f"{JINA}{url}", timeout=25)
                    c.raise_for_status()
                    text = c.text
                    status = "ok"
                except Exception as e:
                    text = f"<!-- Ошибка загрузки: {e} -->"
                    status = "error"
                    logging.warning(f"Jina fail: {url} — {e}")

                safe_name = re.sub(r'[^\w]', '_', url[:70]) + ".md"
                content = (
                    f"<!-- TITLE: {title} -->\n"
                    f"<!-- SOURCE: {url} -->\n"
                    f"<!-- ENGINE: {used_base} -->\n"
                    f"<!-- QUERY: {q} -->\n\n{text}"
                )
                zf.writestr(safe_name, content.encode("utf-8"))

                manifest.append({
                    "query": q,
                    "title": title,
                    "url": url,
                    "status": status,
                    "chars": len(text),
                    "source_engine": used_base or "unknown"
                })
                time.sleep(1.2)

        # Пишем manifest внутрь ZIP
        m = io.StringIO()
        w = csv.DictWriter(m, fieldnames=["query", "title", "url", "status", "chars", "source_engine"])
        w.writeheader()
        w.writerows(manifest)
        zf.writestr("manifest.csv", m.getvalue().encode("utf-8"))

    zip_size = zip_buffer.tell()
    logging.info(f"DONE | размер ZIP: {zip_size} байт | записей: {len(manifest)}")
    zip_buffer.seek(0)

    return send_file(
        zip_buffer,
        mimetype="application/zip",
        as_attachment=True,
        download_name=f"{corpus_name}.zip"
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
