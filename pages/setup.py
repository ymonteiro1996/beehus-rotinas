from flask import Blueprint, render_template, request, jsonify, redirect
from pymongo import MongoClient
import certifi
import db as db_module

bp = Blueprint("setup", __name__)


@bp.route("/setup")
def setup_page():
    return render_template(
        "setup.html",
        windows_user=db_module.get_windows_user(),
        already_registered=db_module.db._ready(),
    )


@bp.route("/api/setup/test-connection", methods=["POST"])
def test_connection():
    uri = (request.get_json(force=True) or {}).get("uri", "").strip()
    if not uri:
        return jsonify({"ok": False, "error": "URI não pode ser vazia."})
    try:
        c = MongoClient(uri, serverSelectionTimeoutMS=5000, tlsCAFile=certifi.where())
        c.server_info()
        c.close()
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@bp.route("/api/setup/save-connection", methods=["POST"])
def save_connection():
    uri = (request.get_json(force=True) or {}).get("uri", "").strip()
    if not uri:
        return jsonify({"ok": False, "error": "URI não pode ser vazia."})

    # Test before saving
    try:
        new_client = MongoClient(uri, serverSelectionTimeoutMS=5000, tlsCAFile=certifi.where())
        new_client.server_info()
    except Exception as e:
        return jsonify({"ok": False, "error": f"Conexão falhou: {e}"})

    # Persist to user_connections.json
    conns = db_module.load_user_connections()
    conns[db_module.get_windows_user()] = uri
    db_module.save_user_connections(conns)

    # Re-initialize the db proxy in-place so all existing importers see the live db
    db_module.client = new_client
    db_module.db._init(new_client[db_module.DB_NAME])

    return jsonify({"ok": True})
