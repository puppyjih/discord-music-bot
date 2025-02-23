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

# Suppress noise about console usage from errors
youtube_dl.utils.bug_reports_message = lambda: ''

# ✅ 플레이리스트를 올바르게 가져오기 위해 noplaylist=False 설정
ytdl_format_options = {
    'format': 'bestaudio/best',
    'outtmpl': '%(extractor)s-%(id)s-%(title)s.%(ext)s',
    'restrictfilenames': True,
    'noplaylist': False,  # 🔹 플레이리스트 지원 활성화
    'nocheckcertificate': True,
    'ignoreerrors': True,  # 🔹 오류가 발생해도 계속 진행
    'logtostderr': False,
    'quiet': True,
    'no_warnings': True,
    'default_search': 'auto',
    'source_address': '0.0.0.0',
    'extract_flat': False,  # 🔹 메타데이터만 가져오지 않고 실제 URL을 파싱
    'skip_download': True,  # 🔹 영상 다운로드 없이 메타데이터만 가져오기
    'force_generic_extractor': False,  # 🔹 유튜브 관련 API 우선 사용
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
        """유튜브 URL에서 단일 곡 또는 플레이리스트를 가져오는 함수"""
        loop = loop or asyncio.get_event_loop()

        
        ytdl = youtube_dl.YoutubeDL(ytdl_format_options)

        try:
            data = await loop.run_in_executor(None, lambda: ytdl.extract_info(url, download=not stream))
            print(data)
        except Exception as e:
            print(f"❌ YTDL 에러 발생: {e}")  # 오류 출력
            return []

        # ✅ 플레이리스트일 경우: 삭제된 곡 및 비공개 곡을 자동으로 건너뜀
        if "entries" in data:
            valid_entries = [
                entry for entry in data["entries"]
                if entry and "url" in entry
                and entry.get("availability", "public") != "private"  # 🔹 비공개 곡 제외
                and not entry.get("requires_premium", False)  # 🔹 프리미엄 전용 곡 제외
            ]
            
            if not valid_entries:
                print("⚠️ 플레이리스트 내 유효한 곡이 없음 (모두 삭제됨 또는 프리미엄 전용)")
                return []

            return [
                cls(discord.FFmpegPCMAudio(entry["url"], **ffmpeg_options), data=entry)
                for entry in valid_entries
            ]

        # ✅ 단일 곡일 경우: 프리미엄 전용 곡 필터링
        if data.get("requires_premium", False):
            print("⚠️ 프리미엄 전용 영상은 재생할 수 없음")
            return []
        
        # ✅ 관련 영상이 존재하지 않는 경우 기본값 설정
        related_videos = data.get("related_videos", [])
        if not related_videos:
            print("⚠️ 관련 영상이 존재하지 않음 (빈 리스트 반환)")

        return [cls(discord.FFmpegPCMAudio(data["url"], **ffmpeg_options), data=data)] if "url" in data else []


class Music(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.queue = {}  # 🔹 서버별 대기열 관리
        self.current = {}  # 🔹 서버별 현재 재생 곡
        self.is_playing = {}  # 🔹 서버별 재생 여부
        self.loop = {}  # 🔹 서버별 반복 여부
        self.volume = {}  # 🔹 서버별 볼륨 크기
        self.nowplaying_message = {}  # 🔹 서버별 nowplaying 메시지 관리
        self.autoplay = {}  # 🔹 서버별 추천곡 자동 재생 여부 (기본값: ON)

    async def reset_state(self, guild_id):
        """서버별 음악 상태 초기화"""
        if guild_id in self.queue:
            self.queue[guild_id] = asyncio.Queue()  # ✅ 서버별 대기열 초기화
        self.current[guild_id] = None  # ✅ 서버별 현재 재생 곡 초기화
        self.is_playing[guild_id] = False  # ✅ 서버별 재생 상태 초기화
        self.loop[guild_id] = False  # ✅ 서버별 반복 여부 초기화
        self.volume[guild_id] = 100  # ✅ 서버별 볼륨 기본값 설정
        self.autoplay[guild_id] = True   # 🔹 서버별 추천곡 자동 재생 여부 (기본값: ON)
        
        # ✅ 서버별 nowplaying_message 삭제
        if guild_id in self.nowplaying_message:
            del self.nowplaying_message[guild_id]
        
    async def update_UI(self, interaction: discord.Interaction):
        """현재 재생 중 UI 업데이트 (서버별 관리)"""
        guild_id = interaction.guild.id  # ✅ 현재 서버 ID 가져오기
        nowplaying_embed = await self.nowplaying_logic(interaction)

        # ✅ 서버별 nowplaying 메시지를 관리하도록 수정
        if guild_id in self.nowplaying_message:
            # 기존 메시지가 존재하면 업데이트
            if isinstance(nowplaying_embed, str):
                await self.nowplaying_message[guild_id].edit(content=nowplaying_embed, embed=None)
            else:
                await self.nowplaying_message[guild_id].edit(content="", embed=nowplaying_embed)
        else:
            # 새 메시지를 생성하여 저장
            if isinstance(nowplaying_embed, str):
                self.nowplaying_message[guild_id] = await interaction.followup.send(content=nowplaying_embed)
            else:
                self.nowplaying_message[guild_id] = await interaction.followup.send(embed=nowplaying_embed)

    async def join_logic(self, interaction: discord.Interaction):
        """봇이 사용자의 음성 채널에 참가"""
        guild_id = interaction.guild.id  # ✅ 서버별 데이터 유지

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
        """슬래시 명령어로 사용될 join"""
        await interaction.response.defer(ephemeral=True)
        result = await self.join_logic(interaction)  # join_logic() 사용
        await interaction.followup.send(result, ephemeral=True)

    @app_commands.command(name="pplay", description="Play song or playlist")
    @app_commands.describe(url="Put link or name of song here")
    async def play(self, interaction: discord.Interaction, url: str):
        """노래 추가 & 재생 (단일 곡 + 플레이리스트 지원)"""
        guild_id = interaction.guild.id  # ✅ 현재 서버 ID 가져오기

        # ✅ 각 서버의 대기열이 존재하지 않으면 초기화
        if guild_id not in self.queue:
            self.queue[guild_id] = asyncio.Queue()

        print("🔄 Debug: /pplay 명령어 실행됨")

        # ✅ 음성 채널에 자동 입장
        join_result = await self.join_logic(interaction)
        if "🚫" in join_result:
            await interaction.response.send_message(join_result, ephemeral=True)
            return

        await interaction.response.defer()

        voice_client = interaction.guild.voice_client
        if not voice_client or not voice_client.is_connected():
            await interaction.followup.send("❌ 봇이 음성 채널에 연결되지 못했습니다.", ephemeral=True)
            return

        # ✅ 노래 로딩 (단일 곡 또는 플레이리스트)
        try:
            tracks = await YTDLSource.from_url(url, loop=self.bot.loop, stream=True)
            if not tracks:
                await interaction.followup.send("❌ 노래를 가져오는 데 문제가 발생했습니다. URL을 확인해주세요.", ephemeral=True)
                return
        except Exception as e:
            await interaction.followup.send(f"❌ 노래를 불러오는 중 오류 발생: {e}", ephemeral=True)
            return

        # ✅ 플레이리스트일 경우 여러 곡 추가
        for track in tracks:
            await self.queue[guild_id].put(track)

        # # ✅ 플레이리스트인지 단일 곡인지 메시지 출력
        # if len(tracks) > 1:
        #     await interaction.followup.send(f"📜 `{len(tracks)}`개의 곡이 플레이리스트에서 추가되었습니다.", ephemeral=True)
        # else:
        #     await interaction.followup.send(f"🎵 `{tracks[0].title}`을(를) 추가했습니다.", ephemeral=True)

        # ✅ 자동으로 재생 시작
        if not self.is_playing.get(guild_id, False) and not voice_client.is_paused():
            await self.play_next(interaction)
        else:
            await self.update_UI(interaction)

        # ✅ 기존 메시지 삭제
        if self.queue[guild_id].qsize() >= 1:
            try:
                asyncio.create_task(interaction.delete_original_response())
            except discord.NotFound:
                pass

    async def play_next(self, interaction: discord.Interaction):
        """서버별 다음 곡 자동 재생 (추천곡 기능 ON/OFF 반영)"""
        guild_id = interaction.guild.id  # ✅ 현재 서버 ID 가져오기
        
        if guild_id in self.queue and not self.queue[guild_id].empty():
            self.current[guild_id] = await self.queue[guild_id].get()
            self.is_playing[guild_id] = True

            interaction.guild.voice_client.play(
                self.current[guild_id],
                after=lambda e: self.bot.loop.create_task(self.play_next_after(interaction, e))
            )

            await self.update_UI(interaction)
        
        # ✅ 대기열이 비었을 경우 → 추천곡 기능이 켜져 있으면 유튜브 자동 추천곡 추가
        elif self.current[guild_id].related_videos and self.autoplay.get(guild_id, True):
            print(f"{self.current[guild_id].related_videos}")
            related_video = self.current[guild_id].related_videos[0]  # ✅ 첫 번째 추천곡 선택
            related_url = f"https://www.youtube.com/watch?v={related_video['id']}"
            print(f"자동 재생 기능 사용 중. 재생 url : {related_url}")
            try:
                tracks = await YTDLSource.from_url(related_url, loop=self.bot.loop, stream=True)
                if tracks:
                    await self.queue[guild_id].put(tracks[0])  # ✅ 추천곡을 대기열에 추가
                    await interaction.followup.send(f"🎵 자동 추천곡 추가: `{tracks[0].title}`", ephemeral=True)
                    await self.play_next(interaction)  # ✅ 추가된 곡 재생
            except Exception as e:
                await interaction.followup.send(f"❌ 추천곡을 가져오는 중 오류 발생: {e}", ephemeral=True)

        else:
            print(f"{self.current[guild_id].related_videos}")
            await self.update_UI(interaction)

        
    async def play_next_after(self, interaction: discord.Interaction, error):
        """서버별 다음 곡 재생 후 후처리"""
        guild_id = interaction.guild.id  # ✅ 현재 서버 ID 가져오기
        if error:
            print(f"에러 발생: {error}")

        self.is_playing[guild_id] = False  # ✅ 해당 서버만 재생 상태 변경
        await self.play_next(interaction)  # ✅ 서버별 `play_next()` 실행
    
    async def nowplaying_logic(self, interaction: discord.Interaction):
        """현재 재생 중인 노래 정보를 서버별로 표시하는 공통 함수"""
        guild_id = interaction.guild.id  # ✅ 현재 서버 ID 가져오기

        # ✅ 해당 서버에서 봇이 음성 채널에 연결되어 있는지 확인
        if not interaction.guild.voice_client or not interaction.guild.voice_client.is_playing():
            embed = discord.Embed(
                title="■ 정지",
                description="현재 재생 목록이 없어요.",
                color=discord.Color.dark_gray()
            )
            return embed

        # ✅ 서버별 현재 곡 정보 가져오기
        player = self.current.get(guild_id, None)
        if not player:
            return "❌ 현재 곡 정보를 가져올 수 없습니다."

        # ✅ 초 단위를 `HH:MM:SS`로 변환 (datetime.timedelta 사용)
        duration_seconds = player.data.get("duration", 0)  # 기본값 0초
        duration = str(datetime.timedelta(seconds=duration_seconds)) if duration_seconds else "알 수 없음"

        queue_status = f"{self.queue[guild_id].qsize()} 개 대기 중" if not self.queue[guild_id].empty() else "다음 곡 없음"
        channel_name = interaction.guild.voice_client.channel.name

        # ✅ 유튜브 썸네일 및 원본 URL 적용 (서버별로 관리)
        video_url = player.video_url if player.video_url else "https://www.youtube.com/"
        thumbnail_url = player.thumbnail if player.thumbnail else "https://i.imgur.com/Tt6jwFk.png"

        # ✅ 서버별 nowplaying 정보 업데이트
        embed = discord.Embed(
            title="🎵 현재 재생 중",
            description=f"**[{player.title}]({video_url})**",
            color=discord.Color.blue()
        )
        embed.set_thumbnail(url=thumbnail_url)
        embed.add_field(name="노래 길이", value=f"⏳ `{duration}`", inline=True)
        embed.add_field(name="대기중인 곡", value=f"🎶 `{queue_status}`", inline=True)
        embed.add_field(name="채널명", value=f"🔊 `{channel_name}`", inline=True)
        embed.set_footer(text="음악봇 - 디스코드 뮤직 플레이어", icon_url=thumbnail_url)

        return embed  # 🎯 `embed` 반환 (이제 직접 호출 가능!)  
    
    
    @app_commands.command(name="nowplaying", description="Show current playing song")
    async def nowplaying(self, interaction: discord.Interaction):
        """슬래시 명령어(`/nowplaying`)에서 `nowplaying_logic()`을 호출"""
        await interaction.response.defer(ephemeral=True)
        result = await self.nowplaying_logic(interaction)

        if isinstance(result, str):
            await interaction.followup.send(result, ephemeral=True)
        else:
            await interaction.followup.send(embed=result)

    
    @app_commands.command(name="skip", description="Skip current song")
    async def skip(self, interaction: discord.Interaction):
        """현재 노래를 스킵하고 다음 곡을 재생"""
        await interaction.response.defer(ephemeral=True)
        guild_id = interaction.guild.id  # ✅ 서버별 데이터 유지

        voice_client = interaction.guild.voice_client

        if voice_client and voice_client.is_playing():
            voice_client.stop()  # 현재 재생 중인 곡 정지
            await interaction.followup.send("⏭️ 현재 노래를 건너뜁니다.", ephemeral=True)

            # ✅ 다음 곡 재생 시작
            await self.play_next(interaction)
        else:
            await interaction.followup.send("❌ 현재 재생 중인 노래가 없습니다.", ephemeral=True)

    @app_commands.command(name="volume", description="Adjust the volume")
    @app_commands.describe(volume="Set volume (0-100)")
    async def volume(self, interaction: discord.Interaction, volume: int):
        """음악 볼륨 조정"""
        await interaction.response.defer(ephemeral=True)
        guild_id = interaction.guild.id  # ✅ 서버별 데이터 유지

        if not interaction.guild.voice_client or not interaction.guild.voice_client.is_playing():
            await interaction.followup.send("현재 재생 중인 노래가 없습니다.", ephemeral=True)
            return

        if 0 <= volume <= 100:
            if interaction.guild.voice_client.source:
                interaction.guild.voice_client.source.volume = volume / 100
                self.volume[guild_id] = volume  # ✅ 서버별 볼륨 관리
                await interaction.followup.send(f"🔊 볼륨을 {volume}%로 조정했습니다.")
            else:
                await interaction.followup.send("볼륨을 변경할 수 없습니다.", ephemeral=True)
        else:
            await interaction.followup.send("볼륨 값은 0에서 100 사이여야 합니다.", ephemeral=True)

    @app_commands.command(name="stop", description="Leave voice channel")
    async def stop(self, interaction: discord.Interaction):
        """서버별 상태 초기화 후 음성 채널 퇴장"""
        await interaction.response.defer(ephemeral=True)

        guild_id = interaction.guild.id  # ✅ 현재 서버 ID 가져오기

        if interaction.guild.voice_client:
            # ✅ 개별 서버 임베드 메시지 전송
            embed = discord.Embed(
                title="■ 정지",
                description="재생을 정지하고 음성 채널을 나갔어요.",
                color=discord.Color.dark_gray()
            )
            
            # ✅ 서버별 nowplaying 메시지 삭제
            if guild_id in self.nowplaying_message:
                try:
                    await self.nowplaying_message[guild_id].edit(content="", embed=embed)
                except discord.NotFound:
                    pass  # 메시지가 이미 삭제된 경우 무시
            await interaction.followup.send(f"🚫 봇이 `{interaction.guild.voice_client.channel}` 채널에서 퇴장했습니다.", ephemeral=True)
            await self.reset_state(guild_id)            
            await interaction.guild.voice_client.disconnect()
        else:
            await interaction.followup.send("❌ 봇이 현재 음성 채널에 연결되어 있지 않습니다.", ephemeral=True)

    @app_commands.command(name="pause", description="Pause current song")
    async def pause(self, interaction: discord.Interaction):
        """음악 일시정지"""
        await interaction.response.defer(ephemeral=True)
        
        if interaction.guild.voice_client.is_playing():
            interaction.guild.voice_client.pause()
            await interaction.followup.send("음악이 일시 정지되었습니다.")
        else:
            await interaction.followup.send("재생 중인 음악이 없습니다.")

    @app_commands.command(name="resume", description="Resume paused song")
    async def resume(self, interaction: discord.Interaction):
        """음악 다시 재생"""
        await interaction.response.defer(ephemeral=True)

        if interaction.guild.voice_client.is_paused():
            interaction.guild.voice_client.resume()
            await interaction.followup.send("음악이 다시 재생됩니다.")
        else:
            await interaction.followup.send("재생할 음악이 없습니다.")
            
            
    @app_commands.command(name="playlist", description="Show current queue")
    async def playlist(self, interaction: discord.Interaction):
        """대기열 목록 출력"""
        await interaction.response.defer(ephemeral=True)
        guild_id = interaction.guild.id  # ✅ 서버별 데이터 유지

        if guild_id not in self.queue or self.queue[guild_id].empty():
            await interaction.followup.send("📭 대기열이 비어 있습니다.")
            return

        message = "🎶 **플레이리스트:**\n"
        temp_queue = list(self.queue[guild_id]._queue)
        for idx, player in enumerate(temp_queue, start=1):
            message += f"{idx}. {player.title}\n"

        await interaction.followup.send(message)


    @app_commands.command(name="remove", description="Remove a song from queue")
    @app_commands.describe(index="Index of the song to remove")
    async def remove(self, interaction: discord.Interaction, index: int):
        """대기열에서 곡 삭제"""
        await interaction.response.defer(ephemeral=True)
        guild_id = interaction.guild.id  # ✅ 서버별 데이터 유지

        if guild_id not in self.queue or self.queue[guild_id].empty():
            await interaction.followup.send("📭 대기열이 비어 있습니다.")
            return

        temp_queue = list(self.queue[guild_id]._queue)
        if 0 < index <= len(temp_queue):
            removed = temp_queue.pop(index - 1)
            await interaction.followup.send(f"🗑️ 삭제: {removed.title}")

            self.queue[guild_id] = asyncio.Queue()
            for item in temp_queue:
                await self.queue[guild_id].put(item)
        else:
            await interaction.followup.send("❌ 유효한 번호를 입력하세요.")

    @app_commands.command(name="autoplay", description="자동재생기능 'on' 또는 'off'")
    @app_commands.describe(state="Enable or disable autoplay (on/off)")
    async def autoplay(self, interaction: discord.Interaction, state: str):
        """추천곡 자동 재생 기능을 켜거나 끄는 명령어"""
        guild_id = interaction.guild.id  # ✅ 현재 서버 ID 가져오기

        if state.lower() == "on":
            self.autoplay[guild_id] = True
            await interaction.response.send_message("✅ 추천곡 자동 재생이 **활성화**되었습니다.", ephemeral=True)
        elif state.lower() == "off":
            self.autoplay[guild_id] = False
            await interaction.response.send_message("❌ 추천곡 자동 재생이 **비활성화**되었습니다.", ephemeral=True)
        else:
            await interaction.response.send_message("⚠️ 사용법: `/autoplay on` 또는 `/autoplay off`", ephemeral=True)


# 추후 추가 고려할 기능. 버튼을 통해 플레이어 조작      
# @bot.event
# async def on_interaction(interaction: discord.Interaction):
#     """버튼 클릭 이벤트 핸들러"""
#     if interaction.data["custom_id"] == "stop":
#         await bot.get_cog("Music").stop(interaction)
#     elif interaction.data["custom_id"] == "skip":
#         await bot.get_cog("Music").skip(interaction)
#     elif interaction.data["custom_id"] == "pause":
#         await bot.get_cog("Music").pause(interaction)
#     elif interaction.data["custom_id"] == "shuffle":
#         await bot.get_cog("Music").shuffle(interaction)            
            
@bot.event
async def on_ready():
    print(f"{bot.user} 봇 실행!! (ID: {bot.user.id})")
    print("------")

    activity = discord.Activity(type=discord.ActivityType.playing, name="서재원 때리기")
    await bot.change_presence(status=discord.Status.online, activity=activity)
    
    try:
        await bot.tree.sync()  # 서버 제한 없이 모든 서버에서 동기화
        print("✅ 모든 서버에서 슬래시 명령어 동기화 완료!")
    except Exception as e:
        print(f"❌ 슬래시 명령어 동기화 실패: {e}")

async def main():
    async with bot:
        await bot.add_cog(Music(bot))
        await bot.start(os.getenv("discord_token"))

asyncio.run(main())