from sqlalchemy import Column, Integer, String, Boolean, Enum, DateTime, ForeignKey, Text, BigInteger, UniqueConstraint
from sqlalchemy.orm import declarative_base, relationship
from datetime import datetime, timezone
import enum


Base = declarative_base()


def utcnow():
    return datetime.now(timezone.utc)


class GameStatus(enum.Enum):
    lobby = "lobby"
    active = "active"
    finished = "finished"


class Phase(enum.Enum):
    debate = "debate"
    justice = "justice"
    l_reasoning = "l_reasoning"
    kira_judgment = "kira_judgment"


class Role(enum.Enum):
    kira = "kira"
    l = "l"
    worshipper = "worshipper"
    investigator = "investigator"


class ProposalStatus(enum.Enum):
    pending = "pending"
    agreed = "agreed"
    disagreed = "disagreed"
    passed_through = "passed_through"
    reversed = "reversed"


class ArgumentStatus(enum.Enum):
    active = "active"
    resolved = "resolved"


class Tone(enum.Enum):
    weak_accusation = "weak_accusation"
    powerful_accusation = "powerful_accusation"
    gentle_accusation = "gentle_accusation"
    careful_accusation = "careful_accusation"
    weak_commendation = "weak_commendation"
    powerful_commendation = "powerful_commendation"


class RemarkType(enum.Enum):
    raise_suspicion = "raise_suspicion"
    lower_suspicion = "lower_suspicion"
    restore_vp = "restore_vp"
    raise_max_vp = "raise_max_vp"


class InformationType(enum.Enum):
    two_possible_l = "two_possible_l"
    two_not_l = "two_not_l"
    one_worshipper = "one_worshipper"


class Game(Base):
    __tablename__ = "games"
    game_id = Column(Integer, primary_key=True, autoincrement=True)
    guild_id = Column(BigInteger, nullable=False, unique=True)
    channel_id = Column(BigInteger, nullable=False)
    status = Column(Enum(GameStatus), default=GameStatus.lobby, nullable=False)
    current_phase = Column(Enum(Phase))
    current_round = Column(Integer, default=0)
    current_turn_index = Column(Integer, default=0)
    timeout_hours = Column(Integer, default=24)
    created_at = Column(DateTime, default=utcnow)
    players = relationship("Player", back_populates="game", passive_deletes=True)
    turns = relationship("Turn", back_populates="game", passive_deletes=True)
    votes = relationship("Vote", back_populates="game", passive_deletes=True)
    player_count = Column(Integer, default=5, nullable=False)
    skipped_voters = Column(Integer, default=0)
    l_knows_kira = Column(Boolean, default=False)
    l_phase_done = Column(Boolean, default=False)
    kira_phase_target = Column(Integer, default=0)


class Player(Base):
    __tablename__ = "players"
    player_id = Column(Integer, primary_key=True, autoincrement=True)
    game_id = Column(Integer, ForeignKey("games.game_id", ondelete="CASCADE"), nullable=False)
    discord_id = Column(BigInteger, nullable=False)
    display_name = Column(String(64), nullable=False)
    role = Column(Enum(Role))
    suspicion = Column(Integer, default=0)
    vp_current = Column(Integer, default=100)
    vp_max = Column(Integer, default=100)
    turn_order = Column(Integer)
    is_eliminated = Column(Boolean, default=False)
    timeout_at = Column(DateTime)
    game = relationship("Game", back_populates="players")
    alias = Column(String(64))
    __table_args__ = (
        UniqueConstraint("game_id", "discord_id", name="uq_game_player"),
        UniqueConstraint("game_id", "alias", name="uq_game_alias")
        )


class TrustMatrix(Base):
    __tablename__ = "trust_matrix"
    trust_id = Column(Integer, primary_key=True, autoincrement=True)
    game_id = Column(Integer, ForeignKey("games.game_id", ondelete="CASCADE"), nullable=False)
    player_a_id = Column(Integer, ForeignKey("players.player_id", ondelete="CASCADE"), nullable=False)
    player_b_id = Column(Integer, ForeignKey("players.player_id", ondelete="CASCADE"), nullable=False)
    trust_a_to_b = Column(Integer, default=0)
    trust_b_to_a = Column(Integer, default=0)
    player_a = relationship("Player", foreign_keys=[player_a_id])
    player_b = relationship("Player", foreign_keys=[player_b_id])
    __table_args__ = (
        UniqueConstraint("game_id", "player_a_id", "player_b_id", name="uq_trust_pair"),
    )


class Information(Base):
    __tablename__ = "information"
    info_id = Column(Integer, primary_key=True, autoincrement=True)
    game_id = Column(Integer, ForeignKey("games.game_id", ondelete="CASCADE"), nullable=False)
    owner_id = Column(Integer, ForeignKey("players.player_id", ondelete="CASCADE"), nullable=False)
    target_id = Column(Integer, ForeignKey("players.player_id", ondelete="CASCADE"), nullable=False)
    info_type = Column(Enum(InformationType))
    content = Column(Text)
    is_sent = Column(Boolean, default=False)
    owner = relationship("Player", foreign_keys=[owner_id])
    target = relationship("Player", foreign_keys=[target_id])


class Turn(Base):
    __tablename__ = "turns"
    turn_id = Column(Integer, primary_key=True, autoincrement=True)
    game_id = Column(Integer, ForeignKey("games.game_id", ondelete="CASCADE"), nullable=False)
    round = Column(Integer, nullable=False)
    turn_index = Column(Integer, nullable=False)
    player_id = Column(Integer, ForeignKey("players.player_id", ondelete="CASCADE"), nullable=False)
    phase = Column(Enum(Phase), nullable=False)
    action_taken = Column(String(64))
    completed_at = Column(DateTime)
    game = relationship("Game", back_populates="turns")
    player = relationship("Player")
    proposal = relationship("Proposal", back_populates="turn", uselist=False)


class Proposal(Base):
    __tablename__ = "proposals"
    proposal_id = Column(Integer, primary_key=True, autoincrement=True)
    game_id = Column(Integer, ForeignKey("games.game_id", ondelete="CASCADE"), nullable=False)
    turn_id = Column(Integer, ForeignKey("turns.turn_id"), nullable=False)
    proposer_id = Column(Integer, ForeignKey("players.player_id", ondelete="CASCADE"), nullable=False)
    target_id = Column(Integer, ForeignKey("players.player_id", ondelete="CASCADE"), nullable=False)
    tone = Column(Enum(Tone), nullable=False)
    message = Column(Text, nullable=False)
    word_count = Column(Integer, nullable=False)
    vp_spent = Column(Integer, nullable=False)
    effectiveness = Column(Integer, default=0)
    status = Column(Enum(ProposalStatus), default=ProposalStatus.pending)
    turn = relationship("Turn", back_populates="proposal")
    proposer = relationship("Player", foreign_keys=[proposer_id])
    target = relationship("Player", foreign_keys=[target_id])
    argument = relationship("Argument", back_populates="proposal", uselist=False)
    responses = relationship("ProposalResponse", back_populates="proposal")


class ProposalResponse(Base):
    __tablename__ = "proposal_responses"
    response_id = Column(Integer, primary_key=True, autoincrement=True)
    proposal_id = Column(Integer, ForeignKey("proposals.proposal_id", ondelete="CASCADE"), nullable=False)
    player_id = Column(Integer, ForeignKey("players.player_id", ondelete="CASCADE"), nullable=False)
    response = Column(String(16))
    contacted_at = Column(DateTime)
    responded_at = Column(DateTime)
    proposal = relationship("Proposal", back_populates="responses")
    player = relationship("Player")


class Argument(Base):
    __tablename__ = "arguments"
    argument_id = Column(Integer, primary_key=True, autoincrement=True)
    proposal_id = Column(Integer, ForeignKey("proposals.proposal_id", ondelete="CASCADE"), nullable=False)
    defender_id = Column(Integer, ForeignKey("players.player_id", ondelete="CASCADE"), nullable=False)
    attacker_id = Column(Integer, ForeignKey("players.player_id", ondelete="CASCADE"), nullable=False)
    status = Column(Enum(ArgumentStatus), default=ArgumentStatus.active)
    current_rebuttal_number = Column(Integer, default=1)
    total_word_count = Column(Integer, default=0)
    proposal = relationship("Proposal", back_populates="argument")
    defender = relationship("Player", foreign_keys=[defender_id])
    attacker = relationship("Player", foreign_keys=[attacker_id])
    rebuttals = relationship("Rebuttal", back_populates="argument")


class Rebuttal(Base):
    __tablename__ = "rebuttals"
    rebuttal_id = Column(Integer, primary_key=True, autoincrement=True)
    argument_id = Column(Integer, ForeignKey("arguments.argument_id", ondelete="CASCADE"), nullable=False)
    player_id = Column(Integer, ForeignKey("players.player_id"), nullable=False)
    rebuttal_number = Column(Integer, nullable=False)
    message = Column(Text)
    word_count = Column(Integer, default=0)
    vp_spent = Column(Integer, default=0)
    is_pass = Column(Boolean, default=False)
    created_at = Column(DateTime, default=utcnow)
    argument = relationship("Argument", back_populates="rebuttals")
    player = relationship("Player")


class Vote(Base):
    __tablename__ = "votes"
    vote_id = Column(Integer, primary_key=True, autoincrement=True)
    game_id = Column(Integer, ForeignKey("games.game_id", ondelete="CASCADE"), nullable=False)
    round = Column(Integer, nullable=False)
    voter_id = Column(Integer, ForeignKey("players.player_id"), nullable=False)
    target_id = Column(Integer, ForeignKey("players.player_id"), nullable=False)
    game = relationship("Game", back_populates="votes")
    voter = relationship("Player", foreign_keys=[voter_id])
    target = relationship("Player", foreign_keys=[target_id])
    __table_args__ = (
        UniqueConstraint("game_id", "round", "voter_id", name="uq_vote_per_round"),
    )

