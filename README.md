# L's Game

A Discord bot implementation of **Death Note: L's Game**, a social deduction game for 5–8 players inspired by Death Note (Ohba/Obata)

---

## Prerequisites

- Python 3.11+
- MariaDB
- A Discord bot token with `bot`, `application.commands`, and `members` (privileged) intents enabled

---

## Setup

```bash
git clone https://github.com/neelophile/ls-game
cd ls-game
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

Create a `.env` file:
```
TOKEN=your_bot_token
GUILD=your_guild_id
URI=mysql+pymysql://user:password@localhost/lsgame
```

Set up the database:
```bash
alembic upgrade head
```

Run:
```bash
python bot.py
```

---

## Commands

| Command | Who | Description |
|---|---|---|
| `/setup [players] [timeout]` | Admin | Create a lobby. Players: 5–8 (default 6). Timeout: hours per turn (default 24). |
| `/join` | Anyone | Join the lobby. DMs must be open. |
| `/start` | Admin | Start the game and assign roles. |
| `/end` | Admin | End the current game. |
| `/forfeit @player` | Admin | Force-forfeit a player. Their turns auto-pass. |
| `/resume` | Admin | Re-prompt current phase after a bot restart. |
| `/ping` | Anyone | Check bot latency. |

---

## How to Play

Refer to `Death_Note_L's_Game.pdf` included in this repository.

---

## Credits

Game design: neelophile
Based on *Death Note* by Tsugumi Ohba and Takeshi Obata

