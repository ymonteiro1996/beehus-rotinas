import os
import subprocess
from flask import Flask, redirect, request, jsonify
from pages.dashboard          import bp as dashboard_bp
from pages.controle_carteiras import bp as controle_carteiras_bp
from pages.controle_demandas  import bp as controle_demandas_bp
from pages.rotinas_diarias        import bp as rotinas_diarias_bp
from pages.rotinas_v2             import bp as rotinas_v2_bp
from pages.banda_rentabilidades   import bp as banda_rentabilidades_bp
from pages.saude_operacional  import bp as saude_operacional_bp
from pages.setup              import bp as setup_bp
# nav and config kept for internal API endpoints used by the pages above
from pages.nav    import bp as nav_bp
from pages.config import bp as config_bp
import db as db_module
from beehus_api import (set_token, clear_token, token_status, verify_token,
                        BeehusAPIError, BeehusAuthError)

app = Flask(__name__)
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 31536000  # 1 year cache for static files
app.config["TEMPLATES_AUTO_RELOAD"] = True
app.register_blueprint(dashboard_bp)
app.register_blueprint(controle_carteiras_bp)
app.register_blueprint(controle_demandas_bp)
app.register_blueprint(rotinas_diarias_bp)
app.register_blueprint(rotinas_v2_bp)
app.register_blueprint(saude_operacional_bp)
app.register_blueprint(setup_bp)
app.register_blueprint(banda_rentabilidades_bp)
app.register_blueprint(nav_bp)
app.register_blueprint(config_bp)


@app.before_request
def require_registration():
    """Redirect unregistered users to /setup before any other route is served.

    O token da API Beehus (seção 8 do CLAUDE.md) é independente do registro do
    Mongo — por isso as rotas de token sempre passam, mesmo sem Mongo
    registrado (o Mongo ainda é exigido hoje só para os 2 gaps documentados em
    `db.py`: issues e "última publicação em lote")."""
    if db_module.db._ready():
        return  # connected — proceed normally
    # Allow setup routes, the Beehus API token modal and static files to pass through
    if (request.path.startswith("/setup") or request.path.startswith("/api/setup")
            or request.path.startswith("/api/beehus-token") or request.path.startswith("/static")):
        return
    return redirect("/setup")


@app.route("/api/beehus-token", methods=["GET"])
def beehus_token_get():
    """Contexto:
    Devolve o status do token atual (carregado? expirado? rejeitado pela
    API?) — usado no boot de toda página (beehus_token.js) pra decidir se
    abre o modal automaticamente, e pelo indicador da masthead. NUNCA
    devolve o token em si. Retorna o dict de beehus_api.token_status().

    Pseudocódigo:
      1. Delega direto pra beehus_api.token_status().
    """
    return jsonify(token_status())


@app.route("/api/beehus-token", methods=["POST"])
def beehus_token_set():
    """Contexto:
    Cola o token do dia — chamada pelo modal "🔑 Beehus API" da masthead
    (beehus_token.js). Valida contra a API IMEDIATAMENTE (verify_token(), 1
    GET barato) em vez de deixar todo o resto das telas falhar
    silenciosamente com um token errado/incompleto. Retorna token_status()
    (200) ou {"error": ...} (400/401).

    Pseudocódigo:
      1. Lê `token` do body JSON; vazio -> 400.
      2. set_token() (guarda em memória + persiste em ~/.swat/beehus.token).
      3. verify_token() — 401/403 -> 401 com mensagem clara; qualquer outro
         erro de rede/API -> aviso "soft" (o token pode estar certo, a API
         só não respondeu agora) em vez de bloquear o salvamento.
    """
    body = request.get_json(force=True, silent=True) or {}
    try:
        set_token(body.get("token", ""))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    try:
        verify_token()
    except BeehusAuthError as exc:
        return jsonify({
            "error": "Token rejeitado pela API (401/403). Confira se copiou o token de hoje por completo.",
            "upstream_status": exc.status,
        }), 401
    except BeehusAPIError as exc:
        return jsonify({**token_status(), "warning": f"Não foi possível validar agora: {exc}"})
    return jsonify(token_status())


@app.route("/api/beehus-token", methods=["DELETE"])
def beehus_token_clear():
    """Contexto: remove o token (memória + disco) — botão de limpar do modal,
    se a pessoa quiser trocar de conta. Retorna token_status().

    Pseudocódigo:
      1. Delega pra beehus_api.clear_token().
    """
    clear_token()
    return jsonify(token_status())


_BASE_DIR = os.path.dirname(os.path.abspath(__file__))

@app.route("/api/update")
def api_update():
    """Pull latest code from GitHub and restart the server."""
    try:
        result = subprocess.run(
            ["git", "pull"],
            capture_output=True, text=True, cwd=_BASE_DIR, timeout=30
        )
        already_latest = "Already up to date" in result.stdout or "Já está atualizado" in result.stdout
        if result.returncode != 0:
            return jsonify({"status": "error", "message": result.stderr or "Falha no git pull"})
        if already_latest:
            return jsonify({"status": "up_to_date", "message": "Já está na versão mais recente."})
        return jsonify({"status": "updated", "message": "Código atualizado!"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})


if __name__ == "__main__":
    import webbrowser, threading
    port = int(os.environ.get("PORT", 5002))
    threading.Timer(1.5, lambda: webbrowser.open(f"http://localhost:{port}")).start()
    app.run(host="0.0.0.0", port=port, use_reloader=False, threaded=True)
