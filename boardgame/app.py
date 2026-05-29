"""Board game catalog + play-session manager (Flask).

Run locally:
    cd boardgame
    pip install -r requirements.txt
    python seed.py        # create boardgames.db and load data/games.json
    python app.py         # http://127.0.0.1:5000
"""
from __future__ import annotations

import os

from flask import (
    Flask,
    abort,
    flash,
    g,
    redirect,
    render_template,
    request,
    url_for,
)

import secrets

import ai_rules
import ai_search
import storage
from models import Game, Participant, PlaySession, UserGameRecord, db

CLIENT_COOKIE = "bg_client_id"

BASE_DIR = os.path.abspath(os.path.dirname(__file__))

# GCS-backed persistence (optional). When GCS_BUCKET is set we keep the SQLite
# file in the bucket: download on boot, re-upload after writes. This survives
# Cloud Run's ephemeral disk without needing Cloud SQL.
GCS_BUCKET = os.getenv("GCS_BUCKET", "")
GCS_DB_BLOB = os.getenv("GCS_DB_BLOB", "boardgame/boardgames.db")
LOCAL_DB_PATH = os.path.join(BASE_DIR, "boardgames.db")


def create_app() -> Flask:
    app = Flask(__name__)
    app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "dev-boardgame-secret")
    app.config["SQLALCHEMY_DATABASE_URI"] = os.getenv(
        "DATABASE_URL", f"sqlite:///{LOCAL_DB_PATH}"
    )
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

    using_gcs = bool(GCS_BUCKET) and "DATABASE_URL" not in os.environ
    if using_gcs:
        # Pull the canonical DB down before SQLAlchemy opens it. Never let a
        # GCS/auth problem crash startup — fall back to a local DB instead.
        try:
            storage.download_db(GCS_BUCKET, GCS_DB_BLOB, LOCAL_DB_PATH)
        except Exception:  # noqa: BLE001
            app.logger.exception("GCS download failed at startup; using local DB")

    db.init_app(app)
    with app.app_context():
        db.create_all()
        # Existing DBs (from older deploys) may be missing newly added columns;
        # create_all() does not alter existing tables, so patch them by hand.
        _migrate_schema()
        # On a fresh database (e.g. first deploy) load the bundled catalog so
        # the list/rules pages are never empty.
        seeded = False
        if os.getenv("AUTO_SEED", "1") == "1" and Game.query.count() == 0:
            _seed_from_json()
            seeded = True
        if using_gcs and seeded:
            # Persist the freshly seeded catalog so the next boot reuses it.
            try:
                _sync_to_gcs()
            except Exception:  # noqa: BLE001 - boot must not fail on GCS errors
                app.logger.exception("GCS sync failed at startup")

    register_routes(app)

    if using_gcs:
        @app.after_request
        def _persist(response):
            # All write routes use POST; sync the DB up to GCS after them.
            if request.method == "POST":
                try:
                    _sync_to_gcs()
                except Exception:  # noqa: BLE001 - never break the response
                    app.logger.exception("Failed to sync DB to GCS")
            return response

    return app


def _migrate_schema() -> None:
    """Add columns that newer code expects but an older SQLite DB may lack.

    SQLAlchemy's create_all() creates missing tables but never alters existing
    ones, so a DB created before `image_url` was introduced would still error
    on queries. We additively `ALTER TABLE` any missing columns. Safe to run on
    every boot (it no-ops when the columns already exist).
    """
    from sqlalchemy import inspect, text

    inspector = inspect(db.engine)
    try:
        existing_tables = set(inspector.get_table_names())
    except Exception:  # noqa: BLE001
        return

    # game.image_url (added with the images feature)
    if "games" in existing_tables:
        cols = {c["name"] for c in inspector.get_columns("games")}
        if "image_url" not in cols:
            with db.engine.begin() as conn:
                conn.execute(text(
                    "ALTER TABLE games ADD COLUMN image_url VARCHAR(800) DEFAULT ''"
                ))


def _sync_to_gcs() -> None:
    storage.upload_db(GCS_BUCKET, GCS_DB_BLOB, LOCAL_DB_PATH)


def _seed_from_json() -> None:
    """Load data/games.json into an empty database. Best-effort."""
    import json

    path = os.path.join(BASE_DIR, "data", "games.json")
    try:
        with open(path, encoding="utf-8") as f:
            entries = json.load(f)
    except Exception:  # noqa: BLE001 - missing/invalid data file is non-fatal
        return
    for entry in entries:
        db.session.add(Game(**entry))
    db.session.commit()


# --- sorting options exposed in the UI --------------------------------------
SORTS = {
    "name": ("名前順", Game.name.asc()),
    "players": ("最大人数が多い順", Game.max_players.desc()),
    "time": ("プレイ時間が短い順", Game.play_time_min.asc()),
    "difficulty": ("難易度が低い順", Game.difficulty.asc()),
    "newest": ("登録が新しい順", Game.created_at.desc()),
}


def _records_by_game(client_id: str) -> dict:
    """Map game_id -> UserGameRecord for the given browser."""
    if not client_id:
        return {}
    rows = UserGameRecord.query.filter_by(client_id=client_id).all()
    return {r.game_id: r for r in rows}


def _get_or_create_record(client_id: str, game_id: int) -> UserGameRecord:
    rec = UserGameRecord.query.filter_by(
        client_id=client_id, game_id=game_id
    ).first()
    if rec is None:
        rec = UserGameRecord(client_id=client_id, game_id=game_id)
        db.session.add(rec)
    return rec


def _cleanup_or_commit(rec: UserGameRecord) -> None:
    """Drop empty records so they don't accumulate, otherwise persist."""
    if rec.id is not None and rec.is_empty:
        db.session.delete(rec)
    db.session.commit()


def register_routes(app: Flask) -> None:
    @app.before_request
    def _ensure_client_id():
        # Anonymous per-browser id for favorites/played records (no login).
        cid = request.cookies.get(CLIENT_COOKIE)
        g.client_id = cid or secrets.token_hex(16)
        g.set_client_cookie = cid is None

    @app.after_request
    def _persist_client_id(response):
        if getattr(g, "set_client_cookie", False):
            response.set_cookie(
                CLIENT_COOKIE, g.client_id,
                max_age=60 * 60 * 24 * 365 * 5,  # 5 years
                samesite="Lax",
            )
        return response

    @app.route("/")
    def index():
        q = (request.args.get("q") or "").strip()
        gtype = (request.args.get("type") or "").strip()
        players = request.args.get("players", type=int)
        sort = request.args.get("sort") or "name"
        show = (request.args.get("show") or "").strip()  # "fav" | "played" | ""

        records = _records_by_game(g.client_id)

        query = Game.query
        if q:
            like = f"%{q}%"
            query = query.filter(
                db.or_(Game.name.ilike(like), Game.name_en.ilike(like),
                       Game.tags.ilike(like))
            )
        if gtype:
            query = query.filter(Game.game_type == gtype)
        if players:
            query = query.filter(
                Game.min_players <= players, Game.max_players >= players
            )

        order = SORTS.get(sort, SORTS["name"])[1]
        games = query.order_by(order).all()

        # "お気に入り/プレイ済みのみ" は記録に基づくのでPython側で絞り込む。
        if show == "fav":
            games = [gm for gm in games
                     if gm.id in records and records[gm.id].favorite]
        elif show == "played":
            games = [gm for gm in games
                     if gm.id in records and records[gm.id].played]

        types = [
            t[0]
            for t in db.session.query(Game.game_type)
            .distinct()
            .order_by(Game.game_type)
            .all()
        ]
        return render_template(
            "index.html",
            games=games,
            records=records,
            types=types,
            sorts=SORTS,
            q=q,
            gtype=gtype,
            players=players,
            sort=sort,
            show=show,
            total=Game.query.count(),
            fav_count=sum(1 for r in records.values() if r.favorite),
            played_count=sum(1 for r in records.values() if r.played),
            ai_available=ai_search.is_available(),
        )

    @app.route("/search")
    def ai_search_view():
        """Natural-language search: "カタンと似たゲーム" など。"""
        nlq = (request.args.get("nlq") or "").strip()
        results = None        # list of (Game, reason)
        interpretation = None
        error = None

        if nlq:
            if not ai_search.is_available():
                error = (
                    "AI検索は利用できません（ANTHROPIC_API_KEY を設定してください）。"
                    "通常のキーワード検索をお使いください。"
                )
            else:
                try:
                    all_games = Game.query.all()
                    by_id = {g.id: g for g in all_games}
                    res = ai_search.search(nlq, all_games)
                    interpretation = res.interpretation
                    results = [
                        (by_id[h.id], h.reason)
                        for h in res.hits
                        if h.id in by_id
                    ]
                except Exception as e:  # noqa: BLE001
                    error = f"AI検索に失敗しました: {e}"

        return render_template(
            "search.html",
            nlq=nlq,
            results=results,
            interpretation=interpretation,
            error=error,
            ai_available=ai_search.is_available(),
        )

    @app.route("/game/<int:game_id>")
    def game_detail(game_id):
        game = Game.query.get_or_404(game_id)
        record = UserGameRecord.query.filter_by(
            client_id=g.client_id, game_id=game_id
        ).first()
        return render_template("detail.html", game=game, record=record)

    # --- favorite / played records (per browser, no login) ------------------
    @app.route("/game/<int:game_id>/favorite", methods=["POST"])
    def toggle_favorite(game_id):
        Game.query.get_or_404(game_id)
        rec = _get_or_create_record(g.client_id, game_id)
        rec.favorite = not rec.favorite
        _cleanup_or_commit(rec)
        return redirect(request.form.get("next") or url_for("game_detail",
                                                            game_id=game_id))

    @app.route("/game/<int:game_id>/played", methods=["POST"])
    def toggle_played(game_id):
        Game.query.get_or_404(game_id)
        rec = _get_or_create_record(g.client_id, game_id)
        rec.played = not rec.played
        _cleanup_or_commit(rec)
        return redirect(request.form.get("next") or url_for("game_detail",
                                                            game_id=game_id))

    @app.route("/game/<int:game_id>/record", methods=["POST"])
    def save_record(game_id):
        """Save rating + memo (and implicitly mark as played)."""
        Game.query.get_or_404(game_id)
        rec = _get_or_create_record(g.client_id, game_id)
        rating = request.form.get("rating", type=int)
        rec.rating = rating if rating and 1 <= rating <= 5 else None
        rec.memo = (request.form.get("memo") or "").strip()
        if rec.rating or rec.memo:
            rec.played = True  # 評価/感想があるなら遊んだとみなす
        _cleanup_or_commit(rec)
        flash("記録を保存しました。", "ok")
        return redirect(url_for("game_detail", game_id=game_id))

    @app.route("/game/add", methods=["GET", "POST"])
    def add_game():
        form = {}
        if request.method == "POST":
            action = request.form.get("action")
            form = request.form.to_dict()

            if action == "ai":
                name = (request.form.get("name") or "").strip()
                if not name:
                    flash("AI生成にはゲーム名を入力してください。", "error")
                elif not ai_rules.is_available():
                    flash(
                        "AI生成は利用できません（ANTHROPIC_API_KEY を設定してください）。",
                        "error",
                    )
                else:
                    try:
                        gen = ai_rules.generate(name)
                        form.update({k: v for k, v in gen.items()})
                        form["name"] = name
                        flash("AIで下書きを生成しました。内容を確認・修正して保存してください。", "ok")
                    except Exception as e:  # noqa: BLE001
                        flash(f"AI生成に失敗しました: {e}", "error")
                return render_template("add_game.html", form=form,
                                       ai_available=ai_rules.is_available())

            # Normal save.
            name = (request.form.get("name") or "").strip()
            if not name:
                flash("ゲーム名は必須です。", "error")
                return render_template("add_game.html", form=form,
                                       ai_available=ai_rules.is_available())
            game = Game(
                name=name,
                name_en=request.form.get("name_en", "").strip(),
                game_type=request.form.get("game_type", "その他").strip() or "その他",
                tags=request.form.get("tags", "").strip(),
                min_players=request.form.get("min_players", type=int) or 1,
                max_players=request.form.get("max_players", type=int) or 4,
                play_time_min=request.form.get("play_time_min", type=int) or 30,
                difficulty=request.form.get("difficulty", type=int) or 2,
                designer=request.form.get("designer", "").strip(),
                year=request.form.get("year", type=int),
                description=request.form.get("description", "").strip(),
                objective=request.form.get("objective", "").strip(),
                characters=request.form.get("characters", "").strip(),
                procedure=request.form.get("procedure", "").strip(),
                end_condition=request.form.get("end_condition", "").strip(),
                online_url=request.form.get("online_url", "").strip(),
                bgg_url=request.form.get("bgg_url", "").strip(),
                image_url=request.form.get("image_url", "").strip(),
            )
            db.session.add(game)
            db.session.commit()
            flash(f"「{game.name}」を追加しました。", "ok")
            return redirect(url_for("game_detail", game_id=game.id))

        return render_template("add_game.html", form=form,
                               ai_available=ai_rules.is_available())

    @app.route("/game/<int:game_id>/delete", methods=["POST"])
    def delete_game(game_id):
        game = Game.query.get_or_404(game_id)
        db.session.delete(game)
        db.session.commit()
        flash(f"「{game.name}」を削除しました。", "ok")
        return redirect(url_for("index"))

    # --- play-session / participant management ------------------------------
    @app.route("/sessions")
    def sessions():
        items = PlaySession.query.order_by(PlaySession.scheduled_on.desc()).all()
        games = Game.query.order_by(Game.name).all()
        return render_template("sessions.html", sessions=items, games=games)

    @app.route("/sessions/create", methods=["POST"])
    def create_session():
        title = (request.form.get("title") or "").strip()
        if not title:
            flash("会の名前を入力してください。", "error")
            return redirect(url_for("sessions"))
        s = PlaySession(
            title=title,
            game_id=request.form.get("game_id", type=int),
            notes=request.form.get("notes", "").strip(),
        )
        from datetime import date

        raw = request.form.get("scheduled_on")
        if raw:
            try:
                s.scheduled_on = date.fromisoformat(raw)
            except ValueError:
                pass
        db.session.add(s)
        db.session.commit()
        return redirect(url_for("session_detail", session_id=s.id))

    @app.route("/sessions/<int:session_id>")
    def session_detail(session_id):
        s = PlaySession.query.get_or_404(session_id)
        games = Game.query.order_by(Game.name).all()
        return render_template("session_detail.html", s=s, games=games)

    @app.route("/sessions/<int:session_id>/game", methods=["POST"])
    def set_session_game(session_id):
        s = PlaySession.query.get_or_404(session_id)
        s.game_id = request.form.get("game_id", type=int)
        db.session.commit()
        return redirect(url_for("session_detail", session_id=s.id))

    @app.route("/sessions/<int:session_id>/participants", methods=["POST"])
    def add_participant(session_id):
        s = PlaySession.query.get_or_404(session_id)
        name = (request.form.get("name") or "").strip()
        if name:
            db.session.add(Participant(session_id=s.id, name=name))
            db.session.commit()
        return redirect(url_for("session_detail", session_id=s.id))

    @app.route("/participants/<int:pid>/delete", methods=["POST"])
    def remove_participant(pid):
        p = Participant.query.get_or_404(pid)
        sid = p.session_id
        db.session.delete(p)
        db.session.commit()
        return redirect(url_for("session_detail", session_id=sid))

    @app.route("/sessions/<int:session_id>/delete", methods=["POST"])
    def delete_session(session_id):
        s = PlaySession.query.get_or_404(session_id)
        db.session.delete(s)
        db.session.commit()
        return redirect(url_for("sessions"))

    @app.errorhandler(404)
    def not_found(e):  # noqa: ARG001
        return render_template("404.html"), 404


app = create_app()

if __name__ == "__main__":
    app.run(debug=True, host="127.0.0.1", port=5000)
