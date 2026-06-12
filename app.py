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
from pages.teste              import bp as teste_bp
# nav and config kept for internal API endpoints used by the pages above
from pages.nav    import bp as nav_bp
from pages.config import bp as config_bp
import db as db_module

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
app.register_blueprint(teste_bp)
app.register_blueprint(nav_bp)
app.register_blueprint(config_bp)


@app.before_request
def require_registration():
    """Redirect unregistered users to /setup before any other route is served."""
    if db_module.db._ready():
        return  # connected — proceed normally
    # Allow setup routes and static files to pass through
    if request.path.startswith("/setup") or request.path.startswith("/api/setup") or request.path.startswith("/static"):
        return
    return redirect("/setup")


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
    port = int(os.environ.get("PORT", 5002))
    app.run(host="0.0.0.0", port=port, use_reloader=False, threaded=True)
