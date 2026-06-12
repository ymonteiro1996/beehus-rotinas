from flask import Flask, redirect, request
from pages.dashboard          import bp as dashboard_bp
from pages.controle_carteiras import bp as controle_carteiras_bp
from pages.controle_demandas  import bp as controle_demandas_bp
from pages.rotinas_diarias    import bp as rotinas_diarias_bp
from pages.rotinas_v2         import bp as rotinas_v2_bp
from pages.saude_operacional  import bp as saude_operacional_bp
from pages.setup              import bp as setup_bp
# nav and config kept for internal API endpoints used by the pages above
from pages.nav    import bp as nav_bp
from pages.config import bp as config_bp
import db as db_module

app = Flask(__name__)
app.register_blueprint(dashboard_bp)
app.register_blueprint(controle_carteiras_bp)
app.register_blueprint(controle_demandas_bp)
app.register_blueprint(rotinas_diarias_bp)
app.register_blueprint(rotinas_v2_bp)
app.register_blueprint(saude_operacional_bp)
app.register_blueprint(setup_bp)
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


if __name__ == "__main__":
    app.run(debug=True, port=5002, use_reloader=False)
