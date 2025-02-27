# Discord Music Bot

A Discord Music Bot built using **discord.py v2.x** and **yt-dlp**. This bot supports slash commands and manages playback using a single editable embed message for "now playing". It also features an autoplay chain that sequentially fetches related tracks based on a reference track (the last song added by the user).

## Features

- **Slash Commands**: Supports commands like `/join`, `/pplay`, `/skip`, `/pause`, `/resume`, `/stop`, `/volume`, `/playlist`, `/remove`, `/autoplay`.
- **Autoplay Chain**: Once a user adds a song, the bot uses that as a reference track to sequentially fetch and play related tracks using YouTube mix queries.
- **Efficient UI Management**: A single "now playing" embed message is continuously updated (edited) to reflect the current playback status, preventing duplicate messages.
- **Environment Variable Management**: Uses `python-dotenv` to load the Discord bot token and other configuration from a `.env` file.

## Prerequisites

- Python 3.8+ (Python 3.11 is recommended)
- pip (latest version recommended)
- A Discord Bot Token


## Installation

1. **Clone the Repository**

   ```bash
   git clone https://github.com/yourusername/discord-music-bot.git
   cd discord-music-bot
   ```

2. **Install Dependencies**

   ```bash
   pip install -r requirements.txt
   ```

   *Example `requirements.txt`:*
   ```
   discord.py>=2.0.0
   yt-dlp
   python-dotenv
   ```

3. **Set Up Environment Variables**

   Create a `.env` file in the root directory with the following content:

   ```
   discord_token=YOUR_DISCORD_BOT_TOKEN
   ```

## Usage

Run the bot with:

```bash
python music_bot_v4_20250224.py
```

## Command Overview

- **/join**  
  Connects the bot to your current voice channel.

- **/pplay [url]**  
  Plays a song or playlist from the provided URL (or search query). The last song added is used as the reference track for autoplay.

- **/skip**  
  Skips the current song.

- **/pause** / **/resume**  
  Pauses or resumes playback.

- **/volume [0-100]**  
  Adjusts the playback volume.

- **/stop**  
  Stops playback and disconnects the bot from the voice channel. The existing now playing embed message is deleted to prevent duplicate messages.

- **/playlist**  
  Displays the current playback queue.

- **/remove [index]**  
  Removes the song at the specified index from the queue.

- **/autoplay [on/off]**  
  Enables or disables the autoplay functionality.

## Autoplay Logic

The autoplay feature uses the last user-added song as a reference track. It sequentially fetches related tracks (one at a time) using a YouTube mix query. The `autoplay_index` variable determines which related track to fetch next.

## UI Management

The bot maintains a single "now playing" embed message that is continuously updated via edits. When the `/stop` command is issued, the existing embed is deleted to prevent new duplicate messages from appearing.

## Docker (Optional)

If you prefer to deploy the bot using Docker, you can create a `Dockerfile` similar to the following:

```dockerfile
FROM python:3.11-slim

WORKDIR /app
COPY requirements.txt requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
CMD ["python", "music_bot_v4_20250224.py"]
```

Build and run the Docker container with:

```bash
docker build -t discord-music-bot .
docker run -d --env-file .env discord-music-bot
```
