"""Database models for the board game catalog and play-session manager.

The schema is intentionally small: a `Game` holds the catalog entry plus the
four rule sections the user cares about (objective / characters / procedure /
end condition), and `PlaySession` + `Participant` cover the "who is playing
what" management side.
"""
from __future__ import annotations

from datetime import datetime, date

from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()


class Game(db.Model):
    __tablename__ = "games"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False, index=True)
    name_en = db.Column(db.String(200), default="")

    # Used for the "type" filter on the list page.
    game_type = db.Column(db.String(80), index=True, default="その他")
    tags = db.Column(db.String(300), default="")  # comma-separated keywords

    min_players = db.Column(db.Integer, default=1, index=True)
    max_players = db.Column(db.Integer, default=4, index=True)
    play_time_min = db.Column(db.Integer, default=30)  # typical length, minutes
    difficulty = db.Column(db.Integer, default=2)  # 1 (easy) .. 5 (heavy)

    designer = db.Column(db.String(200), default="")
    year = db.Column(db.Integer)
    description = db.Column(db.Text, default="")

    # The four rule sections requested by the user.
    objective = db.Column(db.Text, default="")     # 目的
    characters = db.Column(db.Text, default="")    # 登場人物・役職
    procedure = db.Column(db.Text, default="")     # ゲームの流れ（1ターンの流れ）
    detailed_rules = db.Column(db.Text, default="")  # アプリ内の詳細説明（長文）
    end_condition = db.Column(db.Text, default="")  # 終了条件

    online_url = db.Column(db.String(500), default="")  # 実際に遊べるサイト
    bgg_url = db.Column(db.String(500), default="")
    rules_url = db.Column(db.String(500), default="")  # 詳しい解説ページ
    image_url = db.Column(db.String(800), default="")  # ゲームの写真

    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    @property
    def player_range(self) -> str:
        if self.min_players == self.max_players:
            return f"{self.min_players}人"
        return f"{self.min_players}〜{self.max_players}人"

    @property
    def tag_list(self) -> list[str]:
        return [t.strip() for t in (self.tags or "").split(",") if t.strip()]

    @property
    def procedure_steps(self) -> list[str]:
        """Procedure is stored as newline-separated steps for clean rendering."""
        return [s.strip() for s in (self.procedure or "").splitlines() if s.strip()]

    def supports_players(self, n: int) -> bool:
        return self.min_players <= n <= self.max_players

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "name_en": self.name_en,
            "game_type": self.game_type,
            "tags": self.tags,
            "min_players": self.min_players,
            "max_players": self.max_players,
            "play_time_min": self.play_time_min,
            "difficulty": self.difficulty,
            "designer": self.designer,
            "year": self.year,
            "description": self.description,
            "objective": self.objective,
            "characters": self.characters,
            "procedure": self.procedure,
            "detailed_rules": self.detailed_rules,
            "end_condition": self.end_condition,
            "online_url": self.online_url,
            "bgg_url": self.bgg_url,
            "rules_url": self.rules_url,
            "image_url": self.image_url,
        }


class PlaySession(db.Model):
    """A planned play of a particular game, with its participants."""

    __tablename__ = "play_sessions"

    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    game_id = db.Column(db.Integer, db.ForeignKey("games.id"), nullable=True)
    scheduled_on = db.Column(db.Date, default=date.today)
    notes = db.Column(db.Text, default="")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    game = db.relationship("Game")
    participants = db.relationship(
        "Participant",
        backref="session",
        cascade="all, delete-orphan",
        order_by="Participant.id",
    )

    @property
    def participant_count(self) -> int:
        return len(self.participants)

    @property
    def fits_game(self) -> bool | None:
        """Whether the current head-count is valid for the chosen game."""
        if not self.game:
            return None
        return self.game.supports_players(self.participant_count)


class Participant(db.Model):
    __tablename__ = "participants"

    id = db.Column(db.Integer, primary_key=True)
    session_id = db.Column(
        db.Integer, db.ForeignKey("play_sessions.id"), nullable=False
    )
    name = db.Column(db.String(120), nullable=False)


class UserGameRecord(db.Model):
    """Per-browser favorite / played record for a game.

    There is no login: each browser gets an anonymous client_id stored in a
    cookie, and we key records by (client_id, game_id). The played record can
    carry a 1-5 star rating and a free-text memo.
    """

    __tablename__ = "user_game_records"
    __table_args__ = (
        db.UniqueConstraint("client_id", "game_id", name="uq_client_game"),
    )

    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.String(40), nullable=False, index=True)
    game_id = db.Column(
        db.Integer, db.ForeignKey("games.id"), nullable=False, index=True
    )

    favorite = db.Column(db.Boolean, default=False)
    played = db.Column(db.Boolean, default=False)
    rating = db.Column(db.Integer)        # 1..5, optional
    memo = db.Column(db.Text, default="")  # 感想（公開レビュー本文）
    nickname = db.Column(db.String(60), default="")  # 公開レビューの表示名
    updated_at = db.Column(db.DateTime, default=datetime.utcnow,
                           onupdate=datetime.utcnow)

    game = db.relationship("Game")

    @property
    def is_empty(self) -> bool:
        """True when nothing worth keeping is recorded (allows cleanup)."""
        return (
            not self.favorite
            and not self.played
            and not self.rating
            and not (self.memo or "").strip()
        )

    @property
    def is_review(self) -> bool:
        """A public review = has a star rating and/or a written comment."""
        return bool(self.rating or (self.memo or "").strip())

    @property
    def display_name(self) -> str:
        return (self.nickname or "").strip() or "名無しさん"
