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

ytdl_format_options = {
    'format': 'bestaudio/best',
    'outtmpl': '%(extractor)s-%(id)s-%(title)s.%(ext)s',
    'restrictfilenames': True,
    'noplaylist': True,
    'nocheckcertificate': True,
    'ignoreerrors': False,
    'logtostderr': False,
    'quiet': True,
    'no_warnings': True,
    'default_search': 'auto',
    'source_address': '0.0.0.0',  # bind to ipv4 since ipv6 addresses cause issues sometimes
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
        self.title = data.get('title')
        self.url = data.get('url')
        self.video_url = data.get("webpage_url", "https://www.youtube.com/")  # 🔹 유튜브 원본 링크 추가
        self.thumbnail = data.get("thumbnail", "https://i.imgur.com/Tt6jwFk.png")  # 🔹 썸네일 추가

    @classmethod
    async def from_url(cls, url, *, loop=None, stream=False):
        loop = loop or asyncio.get_event_loop()
        data = await loop.run_in_executor(None, lambda: ytdl.extract_info(url, download=not stream))

        if 'entries' in data:
            data = data['entries'][0]

        filename = data['url'] if stream else ytdl.prepare_filename(data)
        return cls(discord.FFmpegPCMAudio(filename, **ffmpeg_options), data=data)

class Music(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.queue = {}  # 🔹 서버별 대기열 관리
        self.current = {}  # 🔹 서버별 현재 재생 곡
        self.is_playing = {}  # 🔹 서버별 재생 여부
        self.loop = {}  # 🔹 서버별 반복 여부
        self.volume = {}  # 🔹 서버별 볼륨 크기
        self.nowplaying_message = {}  # 🔹 서버별 nowplaying 메시지 관리
        
    async def reset_state(self, guild_id):
        """서버별 음악 상태 초기화"""
        self.queue[guild_id] = asyncio.Queue()
        self.current[guild_id] = None
        self.is_playing[guild_id] = False
        self.loop[guild_id] = False
        self.volume[guild_id] = 100
        if guild_id in self.nowplaying_message:
            del self.nowplaying_message[guild_id]  # 🔹 nowplaying 메시지 삭제
        
    async def update_UI(self, interaction: discord.Interaction):
    # ✅ `nowplaying_logic()`을 직접 호출하여 메시지를 업데이트
        nowplaying_embed = await self.nowplaying_logic(interaction)
        
        if interaction.guild.voice_client.is_connected():
            if self.nowplaying_message:
                if isinstance(nowplaying_embed, str):
                    await self.nowplaying_message.edit(content=nowplaying_embed)   
                else:
                    await self.nowplaying_message.edit(content="", embed=nowplaying_embed)    
            else:
                if isinstance(nowplaying_embed, str):
                    self.nowplaying_message = await interaction.followup.send(content=nowplaying_embed)   
                else:
                    self.nowplaying_message = await interaction.followup.send(content="", embed=nowplaying_embed)

    async def join_logic(self, interaction: discord.Interaction):
        """봇이 사용자의 음성 채널에 참가 (명령어가 아닌 일반 함수)"""
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

    @app_commands.command(name="pplay", description="Play song")
    @app_commands.describe(url="Put link or name of song here")
    async def play(self, interaction: discord.Interaction, url: str):
        """노래 추가 & 재생 (하나의 메시지만 유지하면서 업데이트)"""
        
        print("🔄 Debug: /pplay 명령어 실행됨")

        # join()을 직접 호출하는 대신 join_logic()을 사용
        join_result = await self.join_logic(interaction)

        # 봇이 채널에 입장하지 못했다면 중단
        if "🚫" in join_result:
            await interaction.response.send_message(join_result, ephemeral=True)
            return

        # interaction.response.defer() 실행
        await interaction.response.defer()

        # 음성 채널 연결 확인
        voice_client = interaction.guild.voice_client
        if not voice_client or not voice_client.is_connected():
            await interaction.followup.send("❌ 봇이 음성 채널에 연결되지 못했습니다.", ephemeral=True)
            return

        # 노래 로딩
        print(f"🔄 Debug: `{url}`에서 노래 정보를 가져오는 중...")
        try:
            player = await YTDLSource.from_url(url, loop=self.bot.loop, stream=True)
            if player is None:
                await interaction.followup.send("❌ 노래를 가져오는 데 문제가 발생했습니다. URL을 확인해주세요.", ephemeral=True)
                return
        except Exception as e:
            await interaction.followup.send(f"❌ 노래를 불러오는 중 오류 발생: {e}", ephemeral=True)
            return

        # 노래를 대기열에 추가
        await self.queue.put(player)
        # position = self.queue.qsize()


        # 자동으로 재생 시작
        if not self.is_playing and not voice_client.is_paused():
            await self.play_next(interaction)
        else:
            await self.update_UI(interaction)

        # 🔹 **대기열 크기에 따라 메시지 처리**
        if self.queue.qsize() >= 1:  # 🎵 대기열이 1개 이상이면 추가 요청 메시지를 삭제
            try:
                asyncio.create_task(interaction.delete_original_response())
            except discord.NotFound:
                pass  # 메시지가 이미 삭제되었으면 무시

    async def play_next(self, interaction: discord.Interaction):
        if not self.queue.empty():
            self.current = await self.queue.get()
            self.is_playing = True
            interaction.guild.voice_client.play(
                self.current, after=lambda e: self.bot.loop.create_task(self.play_next_after(interaction, e))
            )
            await self.update_UI(interaction)            
        else:
            await self.update_UI(interaction)
            # print("이건안실행하나?")            
            # await self.reset_state() # ✅ 모든 곡이 끝났을 때 상태 초기화
        
    async def play_next_after(self, interaction: discord.Interaction, error):
        if error:
            print(f"에러: {error}")
        self.is_playing = False
        await self.play_next(interaction)
    
    async def nowplaying_logic(self, interaction: discord.Interaction):
        """현재 재생 중인 노래 정보를 표시하는 공통 함수 (슬래시 명령어X)"""

        # 봇이 음성 채널에 연결되어 있는지 확인
        if not interaction.guild.voice_client or not interaction.guild.voice_client.is_playing():
            embed = discord.Embed(
                title="■ 정지",
                description="현재 재생 목록이 없어요.",
                color=discord.Color.dark_gray()
            )
            return embed

        # 현재 곡 정보 가져오기
        player = self.current
        if not player:
            return "❌ 현재 곡 정보를 가져올 수 없습니다."
        
        duration_seconds = player.data.get("duration", 0)  # 기본값 0초
        duration = str(datetime.timedelta(seconds=duration_seconds)) if duration_seconds else "알 수 없음"
        
        queue_status = f"{self.queue.qsize()} 개 대기 중" if not self.queue.empty() else "다음 곡 없음"
        channel_name = interaction.guild.voice_client.channel.name

        # 임베드 메시지 생성
        embed = discord.Embed(
            title="🎵 현재 재생 중",
            description=f"**[{player.title}]({player.video_url})**",
            color=discord.Color.blue()
        )
        embed.set_thumbnail(url=player.thumbnail)  # 썸네일
        embed.add_field(name="노래 길이", value=f"⏳ `{duration}`", inline=True)
        embed.add_field(name="대기중인 곡", value=f"🎶 `{queue_status}`", inline=True)
        embed.add_field(name="채널명", value=f"🔊 `{channel_name}`", inline=True)
        embed.set_footer(text="음악봇 - 디스코드 뮤직 플레이어", icon_url=player.thumbnail)

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

        if not interaction.guild.voice_client or not interaction.guild.voice_client.is_playing():
            await interaction.followup.send("현재 재생 중인 노래가 없습니다.", ephemeral=True)
            return

        if 0 <= volume <= 100:
            if interaction.guild.voice_client.source:
                interaction.guild.voice_client.source.volume = volume / 100
                self.volume = volume
                await interaction.followup.send(f"볼륨을 {volume}%로 조정했습니다.")
            else:
                await interaction.followup.send("볼륨을 변경할 수 없습니다.", ephemeral=True)
        else:
            await interaction.followup.send("볼륨 값은 0에서 100 사이여야 합니다.", ephemeral=True)


    @app_commands.command(name="stop", description="Leave voice channel")
    async def stop(self, interaction: discord.Interaction):
        """음성 채널 퇴장"""
        await interaction.response.defer(ephemeral=True)  # 응답 예약 (숨김 처리)

        embed = discord.Embed(
            title="■ 정지",
            description="재생을 정지하고 음성 채널을 나갔어요.",
            color=discord.Color.dark_gray()
        )
        
        # 봇이 음성 채널에 연결되어 있는지 확인
        if interaction.guild.voice_client:
            await self.nowplaying_message.edit(content="", embed=embed)
            await self.reset_state()
            await interaction.guild.voice_client.disconnect()
            await interaction.followup.send(f"🚫 봇이 `{interaction.user.voice.channel}` 채널에서 퇴장했습니다.", ephemeral=True)
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

        if not self.queue.empty():
            message = "플레이리스트:\n"
            temp_queue = list(self.queue._queue)
            for idx, player in enumerate(temp_queue, start=1):
                message += f"{idx}. {player.title}\n"
            await interaction.followup.send(message)
        else:
            await interaction.followup.send("대기열이 비어 있습니다.")
            

    @app_commands.command(name="remove", description="Remove a song from queue")
    @app_commands.describe(index="Index of the song to remove")
    async def remove(self, interaction: discord.Interaction, index: int):
        """대기열에서 곡 삭제"""
        await interaction.response.defer(ephemeral=True)
        
        if not self.queue.empty():
            temp_queue = list(self.queue._queue)
            if 0 < index <= len(temp_queue):
                removed = temp_queue.pop(index - 1)
                await interaction.followup.send(f"삭제: {removed.title}")

                self.queue = asyncio.Queue()
                for item in temp_queue:
                    await self.queue.put(item)
            else:
                await interaction.followup.send("유효한 번호를 입력하세요.")
        else:
            await interaction.followup.send("대기열이 비어 있습니다.")

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