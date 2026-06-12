from flask import Blueprint, render_template

bp = Blueprint("controle_carteiras", __name__)


@bp.route("/controle-carteiras")
def index():
    return render_template("controle_carteiras.html")
