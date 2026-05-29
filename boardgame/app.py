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
    redirect,
    render_template,
    request,
    url_for,
)

import ai_rules
from models import Game, Participant, PlaySession, db

BASE_DIR = os.path.abspath(os.path.dirname(__file__))


def create_app() -> Flask:
    app = Flask(__name__)
    app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "dev-boardgame-secret")
    db_path = os.path.join(BASE_DIR, "boardgames.db")
    app.config["SQLALCHEMY_DATABASE_URI"] = os.getenv(
        "DATABASE_URL", f"sqlite:///{db_path}"
    )
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

    db.init_app(app)
    with app.app_context():
        db.create_all()

    register_routes(app)
    return app


# --- sorting options exposed in the UI --------------------------------------
SORTS = {
    "name": ("名前順", Game.name.asc()),
    "players": ("最大人数が多い順", Game.max_players.desc()),
    "time": ("プレイ時間が短い順", Game.play_time_min.asc()),
    "difficulty": ("難易度が低い順", Game.difficulty.asc()),
    "newest": ("登録が新しい順", Game.created_at.desc()),
}


def register_routes(app: Flask) -> None:
    @app.route("/")
    def index():
        q = (request.args.get("q") or "").strip()
        gtype = (request.args.get("type") or "").strip()
        players = request.args.get("players", type=int)
        sort = request.args.get("sort") or "name"

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
            types=types,
            sorts=SORTS,
            q=q,
            gtype=gtype,
            players=players,
            sort=sort,
            total=Game.query.count(),
        )

    @app.route("/game/<int:game_id>")
    def game_detail(game_id):
        game = Game.query.get_or_404(game_id)
        return render_template("detail.html", game=game)

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
