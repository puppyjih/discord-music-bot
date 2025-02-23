import asyncio
import discord
from discord import app_commands
from discord.ext import commands
import os
from dotenv import load_dotenv
import yt_dlp as youtube_dl
import datetime

load_dotenv()

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="/", description="봇 사용설명서", intents=intents)

# YTDL 관련 설정
youtube_dl.utils.bug_reports_message = lambda: ''

ytdl_format_options = {
    'format': 'bestaudio/best',
    'outtmpl': '%(extractor)s-%(id)s-%(title)s.%(ext)s',
    'restrictfilenames': True,
    'noplaylist': False,  # 플레이리스트 지원 활성화
    'nocheckcertificate': True,
    'ignoreerrors': True,
    'logtostderr': False,
    'quiet': True,
    'no_warnings': True,
    'default_search': 'auto',
    'source_address': '0.0.0.0',
    'extract_flat': False,
    'skip_download': True,
    'force_generic_extractor': False,
}

ffmpeg_options = {
    'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
    'options': '-vn',
}

ytdl = youtube_dl.YoutubeDL(ytdl_format_options)

class YTDLSource(discord.PCMVolumeTransformer):
    def __init__(self, source, *, data, volume=0.5):
        super().__init__(source, volume)
        self.data = data
        self.title = data.get("title", "제목 없음")
        self.url = data.get("url")
        self.video_url = data.get("webpage_url", "https://www.youtube.com/")
        self.thumbnail = data.get("thumbnail", "https://i.imgur.com/Tt6jwFk.png")
        self.related_videos = data.get("related_videos", [])
    
    @classmethod
    async def from_url(cls, url, *, loop=None, stream=False):
        loop = loop or asyncio.get_event_loop()
        ytdl_inst = youtube_dl.YoutubeDL(ytdl_format_options)
        try:
            data = await loop.run_in_executor(None, lambda: ytdl_inst.extract_info(url, download=not stream))
        except Exception as e:
            print(f"❌ YTDL 에러 발생: {e}")
            return []
        if "entries" in data:
            valid_entries = [entry for entry in data["entries"]
                             if entry and "url" in entry and entry.get("availability", "public") != "private"
                             and not entry.get("requires_premium", False)]
            if not valid_entries:
                print("⚠️ 플레이리스트 내 유효한 곡이 없음 (모두 삭제됨 또는 프리미엄 전용)")
                return []
            return [cls(discord.FFmpegPCMAudio(entry["url"], **ffmpeg_options), data=entry)
                    for entry in valid_entries]
        if data.get("requires_premium", False):
            print("⚠️ 프리미엄 전용 영상은 재생할 수 없음")
            return []
        related_videos = data.get("related_videos", [])
        if not related_videos:
            print("⚠️ 관련 영상이 존재하지 않음 (빈 리스트 반환)")
        return [cls(discord.FFmpegPCMAudio(data["url"], **ffmpeg_options), data=data)] if "url" in data else []
    
    @staticmethod
    def get_youtube_mix_link(video_id):
        return f"https://www.youtube.com/watch?v={video_id}&list=RD{video_id}"
    
    @classmethod
    async def from_mix_url(cls, mix_url, *, loop=None, stream=False, playliststart=1):
        """
        믹스 URL에서 extract_flat 옵션과 함께 'playliststart'만 받아,
        자동으로 playlistend를 (playliststart)로 설정하여 지정된 항목만 조회합니다.
        예: playliststart=2이면 2번 항목만 조회하게 됩니다.
        """
        loop = loop or asyncio.get_event_loop()
        print(f"[DEBUG] from_mix_url 호출됨 with mix_url: {mix_url} (playliststart={playliststart})")
        try:
            mix_options = ytdl_format_options.copy()
            mix_options['extract_flat'] = True
            mix_options['playliststart'] = playliststart
            mix_options['playlistend'] = playliststart  # 정확히 1개 항목만 조회
            ytdl_mix = youtube_dl.YoutubeDL(mix_options)
            data = await loop.run_in_executor(None, lambda: ytdl_mix.extract_info(mix_url, download=False))
        except Exception as e:
            print(f"❌ YTDL 에러 발생: {e}")
            return []
        if "entries" in data:
            entries = data["entries"]
            full_tracks = []
            for idx, entry in enumerate(entries, start=1):
                print(f"[DEBUG] 항목 {idx}: 제목 - {entry.get('title', '제목 없음')}, URL - {entry.get('url')}")
                try:
                    full_info = await loop.run_in_executor(None, lambda: ytdl.extract_info(entry["url"], download=False))
                    if full_info and "url" in full_info:
                        full_tracks.append(cls(discord.FFmpegPCMAudio(full_info["url"], **ffmpeg_options), data=full_info))
                except Exception as e:
                    print(f"[DEBUG] 항목 {idx} 재추출 실패: {e}")
            print(f"[DEBUG] 재추출 후 총 {len(full_tracks)} 개 트랙 확보됨")
            return full_tracks
        print("[DEBUG] 'entries' 키가 데이터에 없습니다.")
        return []

class Music(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.queue = {}              # 서버별 대기열
        self.current = {}            # 현재 재생 곡
        self.last_track = {}         # 마지막 재생 곡 (자동재생 여부 상관없이)
        self.reference_track = {}    # 사용자가 마지막으로 입력한(수동 추가한) 곡 기준
        self.autoplay_index = {}     # 자동재생 검색 인덱스 (초기값 2: 기준 곡 다음부터)
        self.is_playing = {}         # 재생 상태
        self.loop = {}               # 반복 여부
        self.volume = {}             # 볼륨
        self.nowplaying_message = {} # nowplaying 메시지
        self.autoplay = {}           # 자동재생 ON/OFF (기본 ON)
        self.prefetched = {}         # 미리 추출한 관련 곡 캐시
        self.prefetch_lock = {}      # prefetch 작업 동시 실행 방지를 위한 락
    
    async def reset_state(self, guild_id):
        self.queue[guild_id] = asyncio.Queue()
        self.current[guild_id] = None
        self.last_track[guild_id] = None
        self.reference_track[guild_id] = None
        self.autoplay_index[guild_id] = 2
        self.is_playing[guild_id] = False
        self.loop[guild_id] = False
        self.volume[guild_id] = 100
        self.autoplay[guild_id] = True
        self.prefetched[guild_id] = None
        self.prefetch_lock[guild_id] = False
        if guild_id in self.nowplaying_message:
            del self.nowplaying_message[guild_id]
    
    async def update_UI(self, interaction: discord.Interaction):
        guild_id = interaction.guild.id
        nowplaying_embed = await self.nowplaying_logic(interaction)
        if guild_id in self.nowplaying_message:
            try:
                await self.nowplaying_message[guild_id].edit(content="", embed=nowplaying_embed)
            except discord.NotFound:
                self.nowplaying_message[guild_id] = await interaction.followup.send(embed=nowplaying_embed)
        else:
            self.nowplaying_message[guild_id] = await interaction.followup.send(embed=nowplaying_embed)
    
    async def join_logic(self, interaction: discord.Interaction):
        guild_id = interaction.guild.id
        if not interaction.user.voice or not interaction.user.voice.channel:
            return "🚫 음성 채널에 연결되어 있지 않습니다!"
        channel = interaction.user.voice.channel
        voice_client = interaction.guild.voice_client
        if voice_client and voice_client.is_connected():
            if voice_client.channel != channel:
                await voice_client.move_to(channel)
                return f"🔄 `{channel.name}` 채널로 이동했습니다!"
            else:
                return "✅ 이미 해당 음성 채널에 있습니다!"
        else:
            await channel.connect()
            return f"✅ `{channel.name}` 채널에 입장했습니다!"
    
    @app_commands.command(name="join", description="Join 종이봇 in voice channel")
    async def join(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        result = await self.join_logic(interaction)
        await interaction.followup.send(result, ephemeral=True)
    
    @app_commands.command(name="pplay", description="Play song or playlist")
    @app_commands.describe(url="Put link or name of song here")
    async def play(self, interaction: discord.Interaction, url: str):
        guild_id = interaction.guild.id
        if guild_id not in self.queue:
            self.queue[guild_id] = asyncio.Queue()
        print("[DEBUG] /pplay 명령어 실행됨")
        join_result = await self.join_logic(interaction)
        if "🚫" in join_result:
            await interaction.response.send_message(join_result, ephemeral=True)
            return
        await interaction.response.defer()
        voice_client = interaction.guild.voice_client
        if not voice_client or not voice_client.is_connected():
            await interaction.followup.send("❌ 봇이 음성 채널에 연결되지 못했습니다.", ephemeral=True)
            return
        try:
            tracks = await YTDLSource.from_url(url, loop=self.bot.loop, stream=True)
            if not tracks:
                await interaction.followup.send("❌ 노래를 가져오는 데 문제가 발생했습니다. URL을 확인해주세요.", ephemeral=True)
                return
        except Exception as e:
            await interaction.followup.send(f"❌ 노래를 불러오는 중 오류 발생: {e}", ephemeral=True)
            return
        for track in tracks:
            print(f"[DEBUG] 대기열에 추가되는 트랙: {track.title}")
            await self.queue[guild_id].put(track)
        # 사용자가 직접 추가한 마지막 곡을 기준(reference_track)으로 저장
        self.reference_track[guild_id] = tracks[-1]
        self.autoplay_index[guild_id] = 2
        if not self.is_playing.get(guild_id, False) and not voice_client.is_paused():
            await self.play_next(interaction)
        else:
            await self.update_UI(interaction)
        if self.queue[guild_id].qsize() >= 1:
            try:
                asyncio.create_task(interaction.delete_original_response())
            except discord.NotFound:
                pass
    
    async def prefetch_related(self, guild_id, ref_track):
        """
        백그라운드에서 관련 곡을 prefetch할 때,
        사용자가 마지막으로 입력한 곡(ref_track)을 기준으로,
        self.autoplay_index 값을 이용해 단 1개만 조회합니다.
        """
        if self.prefetch_lock.get(guild_id, False):
            return
        self.prefetch_lock[guild_id] = True
        print(f"[DEBUG] 자동재생 검색 기준 곡: {ref_track.title}")
        video_id = ref_track.video_url.split("watch?v=")[-1]
        mix_url = YTDLSource.get_youtube_mix_link(video_id)
        try:
            index = self.autoplay_index.get(guild_id, 2)
            tracks = await YTDLSource.from_mix_url(mix_url, loop=self.bot.loop, stream=True, playliststart=index)
            chosen_track = tracks[0] if tracks else None
            self.prefetched[guild_id] = chosen_track
            if chosen_track:
                print(f"[DEBUG] Prefetched track for guild {guild_id} at index {index}: {chosen_track.title}")
            else:
                print(f"[DEBUG] Prefetched track for guild {guild_id} at index {index}: None")
        except Exception as e:
            print(f"[DEBUG] Prefetch 관련 오류: {e}")
            self.prefetched[guild_id] = None
        self.prefetch_lock[guild_id] = False
    
    async def play_next(self, interaction: discord.Interaction, last_track=None):
        guild_id = interaction.guild.id
        voice_client = interaction.guild.voice_client
        if not voice_client or not voice_client.is_connected():
            print("[DEBUG] 봇이 이미 채널에서 나갔으므로, update_UI를 호출하지 않고 종료")
            return
        
        print(f"[DEBUG] play_next 호출됨 - 대기열 크기: {self.queue[guild_id].qsize()}")
        if not self.queue[guild_id].empty():
            self.current[guild_id] = await self.queue[guild_id].get()
            self.is_playing[guild_id] = True
            print(f"[DEBUG] 재생 중: {self.current[guild_id].title}")
            # 기준 곡은 항상 현재 재생된 곡으로 갱신 (수동 곡일 경우에만 업데이트)
            if not self.current[guild_id].data.get("autoplay"):
                self.reference_track[guild_id] = self.current[guild_id]
                self.autoplay_index[guild_id] = 2
            asyncio.create_task(self.prefetch_related(guild_id, self.reference_track[guild_id]))
            interaction.guild.voice_client.play(
                self.current[guild_id],
                after=lambda e: self.bot.loop.create_task(self.play_next_after(interaction, e))
            )
            await self.update_UI(interaction)
        elif self.autoplay.get(guild_id, True) and self.reference_track.get(guild_id):
            ref_track = self.reference_track[guild_id]
            if self.prefetched.get(guild_id) is not None:
                chosen_track = self.prefetched[guild_id]
                chosen_track.data['autoplay'] = True
                print(f"[DEBUG] Using prefetched track: {chosen_track.title}")
                await self.queue[guild_id].put(chosen_track)
                self.prefetched[guild_id] = None
                self.autoplay_index[guild_id] += 1
                asyncio.create_task(self.prefetch_related(guild_id, ref_track))
                await self.play_next(interaction)
            else:
                print(f"[DEBUG] 캐시에 프리패치된 트랙이 없음, 직접 추출 시도 (기준 곡: {ref_track.title})")
                video_id = ref_track.video_url.split("watch?v=")[-1]
                mix_url = YTDLSource.get_youtube_mix_link(video_id)
                try:
                    index = self.autoplay_index.get(guild_id, 2)
                    tracks = await YTDLSource.from_mix_url(mix_url, loop=self.bot.loop, stream=True, playliststart=index)
                    chosen_track = tracks[0] if tracks else None
                    if chosen_track:
                        chosen_track.data['autoplay'] = True
                        print(f"[DEBUG] 선택된 관련 트랙: {chosen_track.title}")
                        await self.queue[guild_id].put(chosen_track)
                        self.autoplay_index[guild_id] = index + 1
                        await self.play_next(interaction)
                    else:
                        print("[DEBUG] 관련 트랙을 찾지 못함")
                        await self.update_UI(interaction)
                except Exception as e:
                    print(f"[DEBUG] Autoplay 오류: {e}")
                    await self.update_UI(interaction)
        else:
            await self.update_UI(interaction)
    
    async def play_next_after(self, interaction: discord.Interaction, error):
        guild_id = interaction.guild.id
        if error:
            print(f"[DEBUG] 오류: {error}")
        self.is_playing[guild_id] = False
        self.current[guild_id] = None
        await self.play_next(interaction)
    
    async def nowplaying_logic(self, interaction: discord.Interaction):
        guild_id = interaction.guild.id
        if not interaction.guild.voice_client or not interaction.guild.voice_client.is_playing():
            embed = discord.Embed(
                title="■ 정지",
                description="현재 재생 목록이 없어요.",
                color=discord.Color.dark_gray()
            )
            return embed
        player = self.current.get(guild_id)
        if not player:
            return "❌ 현재 곡 정보를 가져올 수 없습니다."
        if not self.queue[guild_id].empty():
            queue_titles = "\n".join([f"{idx+1}. {track.title}" for idx, track in enumerate(list(self.queue[guild_id]._queue))])
        else:
            queue_titles = "대기열이 비어 있습니다."
        title_text = "🎵 현재 자동 재생 중" if player.data.get("autoplay") else "🎵 현재 재생 중"
        embed = discord.Embed(
            title=title_text,
            description=f"**[{player.title}]({player.video_url})**\n\n**대기열:**\n{queue_titles}",
            color=discord.Color.blue()
        )
        embed.set_thumbnail(url=player.thumbnail)
        embed.add_field(name="노래 길이", value=f"⏳ `{str(datetime.timedelta(seconds=player.data.get('duration', 0)))}`", inline=True)
        embed.add_field(name="채널명", value=f"🔊 `{interaction.guild.voice_client.channel.name}`", inline=True)
        embed.add_field(name="재생 방식", value="자동 재생" if player.data.get("autoplay") else "수동 추가", inline=True)
        embed.set_footer(text="음악봇 - 디스코드 뮤직 플레이어", icon_url=player.thumbnail)
        return embed
    
    @app_commands.command(name="nowplaying", description="Show current playing song")
    async def nowplaying(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        result = await self.nowplaying_logic(interaction)
        if isinstance(result, str):
            await interaction.followup.send(result, ephemeral=True)
        else:
            await interaction.followup.send(embed=result)
    
    @app_commands.command(name="skip", description="Skip current song")
    async def skip(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        guild_id = interaction.guild.id
        voice_client = interaction.guild.voice_client
        if voice_client and voice_client.is_playing():
            print("[DEBUG] /skip 호출됨")
            voice_client.stop()
            await interaction.followup.send("⏭️ 노래 건너뜀", ephemeral=True)
        else:
            await interaction.followup.send("❌ 재생 중인 곡 없음", ephemeral=True)
    
    @app_commands.command(name="volume", description="Adjust the volume")
    @app_commands.describe(volume="Set volume (0-100)")
    async def volume(self, interaction: discord.Interaction, volume: int):
        await interaction.response.defer(ephemeral=True)
        guild_id = interaction.guild.id
        if not interaction.guild.voice_client or not interaction.guild.voice_client.is_playing():
            await interaction.followup.send("현재 재생 중인 곡이 없습니다.", ephemeral=True)
            return
        if 0 <= volume <= 100:
            if interaction.guild.voice_client.source:
                interaction.guild.voice_client.source.volume = volume / 100
                await interaction.followup.send(f"🔊 볼륨을 {volume}%로 조정했습니다.", ephemeral=True)
            else:
                await interaction.followup.send("볼륨을 변경할 수 없습니다.", ephemeral=True)
        else:
            await interaction.followup.send("볼륨 값은 0에서 100 사이여야 합니다.", ephemeral=True)
    
    @app_commands.command(name="stop", description="Leave voice channel")
    async def stop(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        guild_id = interaction.guild.id
        if interaction.guild.voice_client:
            embed = discord.Embed(
                title="■ 정지",
                description="재생을 정지하고 음성 채널을 나갔어요.",
                color=discord.Color.dark_gray()
            )
            if guild_id in self.nowplaying_message:
                try:
                    await self.nowplaying_message[guild_id].edit(content="", embed=embed)
                except discord.NotFound:
                    pass
            await interaction.followup.send(f"🚫 봇이 `{interaction.guild.voice_client.channel}` 채널에서 퇴장했습니다.", ephemeral=True)
            await self.reset_state(guild_id)
            await interaction.guild.voice_client.disconnect()
        else:
            await interaction.followup.send("❌ 봇이 현재 음성 채널에 연결되어 있지 않습니다.", ephemeral=True)
    
    @app_commands.command(name="pause", description="Pause current song")
    async def pause(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        if interaction.guild.voice_client.is_playing():
            interaction.guild.voice_client.pause()
            await interaction.followup.send("음악이 일시 정지되었습니다.", ephemeral=True)
        else:
            await interaction.followup.send("재생 중인 음악이 없습니다.", ephemeral=True)
    
    @app_commands.command(name="resume", description="Resume paused song")
    async def resume(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        if interaction.guild.voice_client.is_paused():
            interaction.guild.voice_client.resume()
            await interaction.followup.send("음악이 다시 재생됩니다.", ephemeral=True)
        else:
            await interaction.followup.send("재생할 음악이 없습니다.", ephemeral=True)
    
    @app_commands.command(name="playlist", description="Show current queue")
    async def playlist(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        guild_id = interaction.guild.id
        current_title = self.current[guild_id].title if self.current.get(guild_id) else "없음"
        if not self.queue[guild_id].empty():
            queue_titles = "\n".join([f"{idx+1}. {track.title}" for idx, track in enumerate(list(self.queue[guild_id]._queue))])
        else:
            queue_titles = "대기열이 비어 있습니다."
        message = f"**현재 재생 중:** {current_title}\n**플레이리스트:**\n{queue_titles}"
        await interaction.followup.send(message, ephemeral=True)
    
    @app_commands.command(name="remove", description="Remove a song from queue")
    @app_commands.describe(index="Index of the song to remove")
    async def remove(self, interaction: discord.Interaction, index: int):
        await interaction.response.defer(ephemeral=True)
        guild_id = interaction.guild.id
        if self.queue[guild_id].empty():
            await interaction.followup.send("📭 대기열이 비어 있습니다.", ephemeral=True)
            return
        temp_queue = list(self.queue[guild_id]._queue)
        if 0 < index <= len(temp_queue):
            removed = temp_queue.pop(index - 1)
            await interaction.followup.send(f"🗑️ 삭제: {removed.title}", ephemeral=True)
            self.queue[guild_id] = asyncio.Queue()
            for item in temp_queue:
                await self.queue[guild_id].put(item)
        else:
            await interaction.followup.send("❌ 유효한 번호를 입력하세요.", ephemeral=True)
    
    @app_commands.command(name="autoplay", description="자동재생기능 'on' 또는 'off'")
    @app_commands.describe(state="Enable or disable autoplay (on/off)")
    async def autoplay(self, interaction: discord.Interaction, state: str):
        guild_id = interaction.guild.id
        if state.lower() == "on":
            self.autoplay[guild_id] = True
            await interaction.response.send_message("✅ 추천곡 자동 재생이 **활성화**되었습니다.", ephemeral=True)
        elif state.lower() == "off":
            self.autoplay[guild_id] = False
            await interaction.response.send_message("❌ 추천곡 자동 재생이 **비활성화**되었습니다.", ephemeral=True)
        else:
            await interaction.response.send_message("⚠️ 사용법: `/autoplay on` 또는 `/autoplay off`", ephemeral=True)

@bot.event
async def on_ready():
    print(f"{bot.user} 봇 실행!! (ID: {bot.user.id})")
    print("------")
    activity = discord.Activity(type=discord.ActivityType.playing, name="서재원과")
    await bot.change_presence(status=discord.Status.online, activity=activity)
    try:
        await bot.tree.sync()
        print("✅ 모든 서버에서 슬래시 명령어 동기화 완료!")
    except Exception as e:
        print(f"❌ 슬래시 명령어 동기화 실패: {e}")

async def main():
    async with bot:
        await bot.add_cog(Music(bot))
        await bot.start(os.getenv("discord_token"))

asyncio.run(main())
