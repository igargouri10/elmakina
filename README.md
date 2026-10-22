# ELMAKINA Live

Real-time multiplayer bluffing card game. Custom role artwork included.

## Run locally

```bash
python -m pip install -r requirements.txt
uvicorn server:app --host 0.0.0.0 --port 8000
```

Open http://localhost:8000, create a room, and share the room URL with friends. For testing on phones connected to the same Wi-Fi, use your computer's LAN IP (e.g. `http://192.168.1.10:8000`) instead of `localhost`, allowing port 8000 through your firewall.

## Deploy live on Render

1. Create a GitHub repository containing these files.
2. In Render, use **New > Blueprint** and select the repository. `render.yaml` configures the service.
3. Share the resulting `https://...onrender.com` link. Each participant opens it separately.

**Important limitations:** Room state is held in one server process's RAM. A redeploy, server restart, or sleeping free instance will erase active rooms; do not scale above one instance without moving room state to shared storage. Clients reconnect with saved browser identity, but a disconnected player can stall reaction voting. This is an alpha test, not a production-ready hosted game. The game requires an active internet connection for remote play and no external image service.

### Security and mechanics

* The server alone owns the deck, all hands, coin counts, and turn state. Each WebSocket message receives only its owner's hand.
* Hidden card discards immediately return to the shuffled deck.
* Challenges of Foreign Aid are disallowed; Tax Agent may block it and that block is challengeable.
* Multiple Tax Agent claims against Business Woman are supported, with each individual claim challengeable.
* Unblocked Terrorist attacks remove one card; an unsuccessful challenge or false Colonel block can remove another independently.
* Colonel's accusation costs four coins. A successful accusation removes a matching role; an unsuccessful accusation transfers four coins to the target and replaces the target's hand.
* Role claims that survive challenges exchange the proven card with a fresh one.

This is an interpretation of the evolving house rules. Please test with your group before relying on edge cases.
