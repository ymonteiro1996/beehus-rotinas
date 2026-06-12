import json
import os
import uuid
from datetime import datetime

from flask import Blueprint, jsonify, render_template, request

bp = Blueprint("controle_demandas", __name__)

DATA_FILE      = os.path.join(os.path.dirname(__file__), "..", "data", "controle_demandas.json")
TEMPLATES_FILE = os.path.join(os.path.dirname(__file__), "..", "data", "wallet_templates.json")

_EXTRA_CLIENTES = ["Fincere", "Blue3 Wealth", "Next Wealth", "RTS", "Beehus"]


def _load_clientes() -> list:
    clientes = []
    seen = set()
    if os.path.exists(TEMPLATES_FILE):
        with open(TEMPLATES_FILE, "r", encoding="utf-8") as fh:
            templates = json.load(fh)
        for tmpl in templates:
            partner = tmpl["name"].split(" - ")[0].strip()
            if partner not in seen:
                seen.add(partner)
                clientes.append(partner)
    for extra in _EXTRA_CLIENTES:
        if extra not in seen:
            seen.add(extra)
            clientes.append(extra)
    return sorted(clientes) if clientes else ["Mira", "SMIG", "Fincere", "Blue3", "Blue3 Wealth", "Oikos", "Beehus"]


CLIENTES    = _load_clientes()
PRIORIDADES = ["Alto", "Médio", "Baixo"]
STATUS_OPTS = ["Pendente", "Em Andamento", "Concluído", "Cancelado", "On Hold"]
TIPOS       = ["Operacional", "Sistema", "Sistema Operacional"]
RESPONSAVEIS = [
    "Yuri", "Hulgo", "Gutcha", "Evair", "Kim",
    "Renan", "Leonardo", "Mauricio", "Juan", "Adzo", "Victor",
]

SEED_DEMANDAS = [
    ("Mira",    "Trazer diário atualizado",                                                                              "Alto",  "Yuri",             "04/05/2026"),
    ("Mira",    "Onboarding Mariano",                                                                                    "Alto",  "Yuri",             "04/05/2026"),
    ("Mira",    "Onboarding Isabel",                                                                                     "Alto",  "Yuri",             "04/05/2026"),
    ("Mira",    "Incluir DAF nos agrupamentos",                                                                          "Alto",  "Yuri",             "04/05/2026"),
    ("Mira",    "Apresentação consolidação",                                                                             "Alto",  "Yuri/Hulgo",       "08/04/2026"),
    ("Mira",    "Pontos de correção na tela do cliente",                                                                 "Médio", "Hulgo",            "Alinhado abaixo"),
    ("Mira",    "Inputar manualmente a data da primeira aquisição de cada ativo (mesmo que histórica)",                  "Baixo", "Hulgo",            "01/06/2026"),
    ("Mira",    "Remover os centavos das classes e ativos",                                                              "Baixo", "Hulgo",            "4T2026"),
    ("Mira",    "Incluir a rentabilidade final na descrição do agrupamento e benchmark",                                 "Baixo", "Hulgo",            "01/06/2026"),
    ("Mira",    "O componente quebrou durante a apresentação e dá erro, o cliente não pensa em atualizar",              "Alto",  "Hulgo",            "01/06/2026"),
    ("Mira",    "Padronizar as datas (Deixar em US ou BR mas padronizar)",                                               "Médio", "Hulgo",            "01/06/2026"),
    ("Mira",    "O gráfico de performance deixar na tela de análise de performance também",                              "Médio", "Hulgo",            "4T2026"),
    ("Mira",    "Manter cliente logado na plataforma",                                                                   "Médio", "Hulgo",            "06/07/2026"),
    ("Blue3",   "Processamento diário 423 carteiras",                                                                    "Alto",  "Yuri",             "Diário"),
    ("Blue3",   "Alinhamento dados 05/05/2026",                                                                          "Alto",  "Yuri",             "Toda terça"),
    ("Blue3",   "Alinhar dados para consultoria com o Douglas",                                                          "Médio", "Hulgo",            "07/05/2026"),
    ("Blue3",   "Alinhar onboarding MFO Blue3",                                                                          "Médio", "Hulgo",            "06/05/2026"),
    ("Blue3",   "Criar clientes na tela do parceiro",                                                                    "Alto",  "Gutcha",           "08/05/2026"),
    ("Blue3",   "Ajustar permissionamentos",                                                                             "Alto",  "Hulgo",            "06/05/2026"),
    ("Blue3",   "Coletar posições AWS - AAI",                                                                            "Médio", "Renan/Yuri",       "06/05/2026 - 08/05/2026"),
    ("Blue3",   "Criar controle de publicação de agrupamentos e acompanhamentos",                                        "Médio", "Gutcha/Yuri",      "08/05/2026"),
    ("Oikos",   "Kim iniciar processamento diário",                                                                      "Alto",  "Yuri/Kim",         "Diário"),
    ("Oikos",   "Controle das cargas",                                                                                   "Alto",  "Kim/Adzo",         "Diário"),
    ("Oikos",   "Conciliação",                                                                                           "Alto",  "Kim",              "Diário"),
    ("Oikos",   "Supervisão dos responsáveis das carteiras",                                                             "Alto",  "Kim",              "Diário"),
    ("Oikos",   "Publicação clientes",                                                                                   "Alto",  "Kim",              "Diário"),
    ("SMIG",    "Iniciar cobrança",                                                                                      "Alto",  "Hulgo",            "08/05/2026"),
    ("SMIG",    "Checar correções offshore",                                                                             "Alto",  "Juan/Hulgo",       "08/05/2026"),
    ("SMIG",    "Recebimento fundos do BTG",                                                                             "Médio", "Hulgo/Juan",       "08/05/2026"),
    ("Fincere", "Onboarding 7 clientes (No total serão 35 clientes)",                                                   "Médio", "Evair/Hulgo",      "05/05/2026"),
    ("Fincere", "Cadastrar ativos",                                                                                      "Médio", "Evair",            "05/05/2026"),
    ("Fincere", "Classificar de acordo com a demanda",                                                                   "Médio", "Hulgo/Evair",      "05/05/2026"),
    ("Fincere", "Publicar carteiras",                                                                                    "Médio", "Evair",            "05/05/2026"),
    ("Fincere", "Alinhar rotinas",                                                                                       "Médio", "Yuri/Hulgo/Evair", "TBD"),
    ("Beehus",  "Correção de carteiras inclusas no agrupamento",                                                         "Médio", "Yuri/Renan",       ""),
    ("Beehus",  "Infraestrutura e versionamento plataforma operacional",                                                 "Alto",  "Leonardo",         ""),
    ("Beehus",  "Apresentação operacional Beehus",                                                                       "Baixo", "Yuri/Evair",       ""),
    ("Beehus",  "Subir arquivos em lote",                                                                                "Médio", "Mauricio/Yuri",    ""),
    ("Beehus",  "Scrapping novos bancos",                                                                                "Médio", "Kim/Adzo",         ""),
    ("Beehus",  "Leitura PDF > UnprocessedPosition",                                                                    "Médio", "Kim/Adzo",         ""),
    ("Beehus",  "Alterar fonte de preços de ativos de CD>B3, CVM, Anbima (Yahoo finance para Stock/Fundo Offshore)",    "Médio", "Leonardo",         ""),
    ("Beehus",  "Remover vínculo do BHE com Python",                                                                    "Alto",  "Leonardo",         ""),
    ("Beehus",  "Criar segregação e KPI de rotinas/demandas para operacional",                                           "Alto",  "Yuri/Mauricio",    ""),
    ("Beehus",  "Pedir para o Leonardo remover a criação de provisões do sistema",                                       "Alto",  "Leonardo",         ""),
]

_PRIO_ORDER   = {"Alto": 0, "Médio": 1, "Baixo": 2}
_STATUS_ORDER = {"Pendente": 0, "Em Andamento": 1, "Concluído": 2, "Cancelado": 3, "On Hold": 4}


# ── JSON storage helpers ───────────────────────────────────────────────────────

def _load() -> list:
    if not os.path.exists(DATA_FILE):
        return []
    with open(DATA_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def _save(data: list) -> None:
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _now() -> str:
    return datetime.utcnow().isoformat()


def _seed_if_empty() -> None:
    if os.path.exists(DATA_FILE):
        return
    now = _now()
    docs = [
        {
            "_id":        str(uuid.uuid4()),
            "cliente":    c,
            "demanda":    d,
            "prioridade": p,
            "responsavel":r,
            "deadline":   dl,
            "status":     "Pendente",
            "created_at": now,
            "updated_at": now,
            "comments":   [],
        }
        for c, d, p, r, dl in SEED_DEMANDAS
    ]
    _save(docs)


# ── Routes ────────────────────────────────────────────────────────────────────

@bp.route("/controle-demandas")
def index():
    _seed_if_empty()
    return render_template(
        "controle_demandas.html",
        active="controle_demandas",
        clientes=CLIENTES,
        prioridades=PRIORIDADES,
        status_opts=STATUS_OPTS,
        tipos=TIPOS,
        responsaveis=RESPONSAVEIS,
    )


@bp.route("/api/demandas")
def list_demandas():
    _seed_if_empty()
    docs = _load()

    cliente    = request.args.get("cliente",    "").strip()
    prioridade = request.args.get("prioridade", "").strip()
    responsavel= request.args.get("responsavel","").strip().lower()
    status     = request.args.get("status",     "").strip()
    tipo       = request.args.get("tipo",       "").strip()
    search     = request.args.get("search",     "").strip().lower()

    if cliente:
        docs = [d for d in docs if d.get("cliente") == cliente]
    if prioridade:
        docs = [d for d in docs if d.get("prioridade") == prioridade]
    if responsavel:
        docs = [d for d in docs if responsavel in d.get("responsavel", "").lower()]
    if status:
        docs = [d for d in docs if d.get("status") == status]
    if tipo:
        docs = [d for d in docs if d.get("tipo") == tipo]
    if search:
        docs = [
            d for d in docs
            if search in d.get("demanda", "").lower()
            or search in d.get("responsavel", "").lower()
        ]

    docs.sort(key=lambda d: (
        _STATUS_ORDER.get(d.get("status", ""), 9),
        _PRIO_ORDER.get(d.get("prioridade", ""), 9),
        d.get("cliente", ""),
    ))
    return jsonify(docs)


@bp.route("/api/demandas", methods=["POST"])
def create_demanda():
    data = request.json or {}
    now  = _now()
    doc  = {
        "_id":        str(uuid.uuid4()),
        "cliente":    data.get("cliente", ""),
        "demanda":    data.get("demanda", ""),
        "prioridade": data.get("prioridade", "Médio"),
        "tipo":       data.get("tipo", ""),
        "responsavel":data.get("responsavel", ""),
        "deadline":   data.get("deadline", ""),
        "status":     data.get("status", "Pendente"),
        "created_at": now,
        "updated_at": now,
        "comments":   [],
    }
    docs = _load()
    docs.append(doc)
    _save(docs)
    return jsonify({"id": doc["_id"]})


@bp.route("/api/demandas/<demand_id>", methods=["PATCH"])
def update_demanda(demand_id):
    data    = request.json or {}
    allowed = {"cliente", "demanda", "prioridade", "tipo", "responsavel", "deadline", "status", "progress"}
    docs    = _load()
    for d in docs:
        if d["_id"] == demand_id:
            for k, v in data.items():
                if k in allowed:
                    d[k] = v
            d["updated_at"] = _now()
            break
    _save(docs)
    return jsonify({"ok": True})


@bp.route("/api/demandas/<demand_id>", methods=["DELETE"])
def delete_demanda(demand_id):
    docs = _load()
    docs = [d for d in docs if d["_id"] != demand_id]
    _save(docs)
    return jsonify({"ok": True})


@bp.route("/api/demandas/<demand_id>/comments", methods=["POST"])
def add_comment(demand_id):
    data    = request.json or {}
    comment = {"text": data.get("text", ""), "created_at": _now()}
    docs    = _load()
    for d in docs:
        if d["_id"] == demand_id:
            d.setdefault("comments", []).append(comment)
            d["updated_at"] = _now()
            break
    _save(docs)
    return jsonify({"ok": True})
