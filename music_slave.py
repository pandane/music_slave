import sys
import traceback
import asyncio
import discord
from discord.ext import commands
from credentials import TOKEN, OWNERID


class ListableQueue(asyncio.Queue):
    async def to_list(self):
        queue_copy = []
        while True:
            try:
                elem = self.get_nowait()
            except asyncio.QueueEmpty:
                break
            else:
                queue_copy.append(elem)
        for elem in queue_copy:
            self.put(elem)
        return queue_copy


class QueueState:
    def __init__(self, bot):
        self.current_request = None
        self.voice_client = None
        self.bot = bot
        self.audio_task = self.bot.loop.create_task(self.audio_player_task())
        self.queued_songs = ListableQueue()
        self.next_song = asyncio.Event()
        self.skip_requests = set()

    def is_playing(self):
        if self.voice_client is None or self.current_request is None:
            return False
        player = self.current_request.process_player
        return not player.is_done()

    def get_queued_info(self):
        return self.queued_songs.to_list()

    def skip(self):
        self.skip_requests.clear()
        if self.is_playing():
            self.queued_songs.task_done()
            self.current_request.process_player.stop()

    def toggle_next(self):
        self.bot.loop.call_soon_threadsafe(self.next_song.set)

    async def audio_player_task(self):
        while True:
            self.next_song.clear()
            self.current_request = await self.queued_songs.get()
            await self.current_request.refresh_player(self)
            await self.bot.send_message(self.current_request.channel,
                                        'I\'m currently playing:\n' +
                                        str(self.current_request))
            self.current_request.process_player.start()
            await self.next_song.wait()

    @property
    def process_player(self):
        return self.current_request.process_player


class QueuedRequest:
    def __init__(self, message, player, request_string):
        self.user_requester = message.author
        self.channel = message.channel
        self.process_player = player
        self.options = \
            {
                'default_search': 'auto',
                'quiet': True,
            }

        self.request_string = request_string

    async def refresh_player(self, current_state):
        self.process_player = await current_state.\
            voice_client.create_ytdl_player(
                self.request_string,
                ytdl_options=self.options,
                after=current_state.toggle_next)

    def __str__(self):
        format_string = '**{0.title}**'
        length = self.process_player.duration
        if length:
            format_string = format_string + \
                '\n[{0[0]}m {0[1]}s]'.format(divmod(length, 60))
        format_string += ' by {0.uploader}\nQueued by {1.display_name}'
        return format_string.format(self.process_player, self.user_requester)


class Music:
    votes_to_skip = 3
    """Commands for queuing and streaming music from the internet."""

    def __init__(self, bot):
        self.bot = bot
        self.queue_states = dict()

    def get_queue_state(self, server):
        state = self.queue_states.get(server.id)
        if state is None:
            state = QueueState(self.bot)
            self.queue_states[server.id] = state
        return state

    def __unload(self):
        for state in self.queue_states.values():
            try:
                state.audio_task.cancel()
                if state.voice_client:
                    self.bot.loop.create_task(state.voice_client.disconnect())
            except BaseException:
                pass

    async def join_channel(self, channel):
        voice_client = await self.bot.join_voice_channel(channel)
        queue_state = self.get_queue_state(channel.server)
        queue_state.voice_client = voice_client

    async def on_command_error(self, error, context):
        """The event triggered when an error is raised while invoking a command.
        context   : Context
        error : Exception"""

        if hasattr(context.command, 'on_error'):
            return

        ignored = (commands.CommandNotFound, commands.UserInputError)
        error = getattr(error, 'original', error)

        if isinstance(error, ignored):
            return

        elif isinstance(error, commands.DisabledCommand):
            return await context.send(f'{context.command} has been disabled.')

        elif isinstance(error, commands.NoPrivateMessage):
            try:
                return await context.author.send(
                    f'{context.command} can not be used in Private Messages.')
            except BaseException:
                pass
        print(
            'Ignoring exception in command {}:'.format(
                context.command),
            file=sys.stderr)
        traceback.print_exception(
            type(error),
            error,
            error.__traceback__,
            file=sys.stderr)

    @commands.command(pass_context=True, no_pm=True)
    async def join(self, context, *, channel: discord.Channel):
        """Adds me to the specified voice channel.

        If I am already in a channel, I will move to the given
        channel, preserving the queue and any currently streaming
        audio.
        """
        success = False
        try:
            await self.join_channel(channel)
            success = True
        except discord.ClientException:
            state = self.get_queue_state(context.message.server)
            await state.voice_client.move_to(channel)
            success = True
        except discord.InvalidArgument:
            await self.bot.send_message(
                context.message.channel,
                'I couldn\'t find that channel in this Discord server.')

        if success:
            await self.bot.send_message(
                context.message.channel,
                'I have joined **' + channel.name + '**')

    @join.error
    async def on_join_error(self, error, context):
        if isinstance(error, commands.BadArgument):
            await self.bot.send_message(
                context.message.channel,
                "I couldn\'t find that channel in this Discord server.")

    @commands.command(pass_context=True, no_pm=True)
    async def summon(self, context):
        """Summons me to join your voice channel."""
        user_current_channel = context.message.author.voice_channel
        if user_current_channel is None:
            await self.bot.send_message(
                context.message.channel,
                'You must be in a voice channel to summon me.')
            return False

        state = self.get_queue_state(context.message.server)
        if state.voice_client is None:
            state.voice_client = await self.bot.join_voice_channel(
                user_current_channel)
        else:
            await state.voice_client.move_to(user_current_channel)

        return True

    @commands.command(pass_context=True, no_pm=True)
    async def play(self, context, *, request: str):
        """Plays the request.

        Uses the youtube-dl module to play your request.
        The request can be a search string or a URL. I will
        figure it out. Primarily designed for youtube, but
        I also support Instagram, Twitter and a number of
        other content sites.
        """
        state = self.get_queue_state(context.message.server)
        options = \
            {
                'default_search': 'auto',
                'quiet': True,
            }

        if state.voice_client is None:
            success = await context.invoke(self.summon)
            if not success:
                return
        was_playing = self.get_queue_state(context.message.server).is_playing()
        try:
            player = await state.voice_client.create_ytdl_player(
                request,
                ytdl_options=options,
                after=state.toggle_next)
        except Exception as e:
            format_string = \
                ('I could not process this request. '
                 'Printing trace: ```py\n{}: {}\n```')
            await self.bot.send_message(context.message.channel,
                                        format_string.format(
                                            type(e).__name__, e))
        else:
            if state.current_request is not None:
                player.volume = state.current_request.process_player.volume
            else:
                player.volume = 0.6
            queued_request = QueuedRequest(context.message, player, request)
            if was_playing:
                await self.bot.send_message(
                    context.message.channel,
                    'Added to queue:\n' + str(queued_request))
            await state.queued_songs.put(queued_request)

    @play.error
    async def on_play_error(self, error, context):
        if isinstance(error, commands.MissingRequiredArgument):
            used_prefix = ""
            prefixes = await self.bot._get_prefix(context.message)
            for prefix in prefixes:
                if prefix in context.message.content:
                    used_prefix = prefix
                    break
            await self.bot.send_message(context.message.channel,
                                        ('You need to request '
                                         'something as follows: \n') +
                                        used_prefix + "play <request>")

    @commands.command(pass_context=True, no_pm=True)
    async def volume(self, context, percentage: int):
        """Sets my volume.

        Make sure <level> is a number between 0 and 100,
        representing the percentage.
        """
        state = self.get_queue_state(context.message.server)
        if percentage < 0 or percentage > 100:
            used_prefix = ""
            prefixes = await self.bot._get_prefix(context.message)
            for prefix in prefixes:
                if prefix in context.message.content:
                    used_prefix = prefix
                    break
            await self.bot.send_message(
                context.message.channel,
                ('You need to give me a volume level '
                 'between 0-100 as follows: \n') +
                used_prefix + "volume <percentage>")
            return

        if state.is_playing():
            player = state.process_player
            player.volume = percentage / 100
            await self.bot.send_message(
                context.message.channel,
                'I set the volume to {:.0%}.'.format(player.volume))
        else:
            await self.bot.send_message(
                context.message.channel,
                ('I can\'t set the volume because '
                 'I\'m not playing anything right now.')
            )

    @volume.error
    async def on_volume_error(self, error, context):
        if isinstance(error, commands.BadArgument):
            await self.bot.send_message(
                context.message.channel,
                "You need to give me an integer between 0 and 100.")

    @commands.command(pass_context=True, no_pm=True)
    async def pause(self, context):
        """Pauses my audio."""
        state = self.get_queue_state(context.message.server)
        if state.current_request.process_player._resumed.is_set():
            player = state.process_player
            player.pause()
        else:
            await self.bot.send_message(
                context.message.channel,
                "I\'m not playing anything right now.")

    @commands.command(pass_context=True, no_pm=True)
    async def resume(self, context):
        """Resumes my audio."""
        state = self.get_queue_state(context.message.server)
        if not state.current_request.process_player._resumed.is_set():
            player = state.process_player
            player.resume()
        else:
            await self.bot.send_message(
                context.message.channel,
                "I have nothing to resume.")

    @commands.command(pass_context=True, no_pm=True)
    async def stop(self, context):
        """Dismisses me from the voice channel."""
        server = context.message.server
        state = self.get_queue_state(server)

        if state.is_playing():
            player = state.process_player
            player.stop()

        try:
            state.audio_task.cancel()
            del self.queue_states[server.id]
            await state.voice_client.disconnect()
        except BaseException:
            pass

    @commands.command(pass_context=True, no_pm=True)
    async def skip(self, context):
        """Asks me to skip a song. No promises.

        The song will be skipped immediately if the
        requester asks to skip it.

        The song will not be skipped if it was requested
        by my owner, unless he requests to skip it.
        """
        state = self.get_queue_state(context.message.server)
        if not state.is_playing():
            if state.current_request.process_player.after is None:
                await self.bot.send_message(
                    context.message.channel,
                    'I\'m not playing anything right now.')
            else:
                await self.bot.send_message(
                    context.message.channel,
                    'Skipping...')
                state.current_request.process_player.stop()
            return

        voter = context.message.author
        if voter == state.current_request.user_requester or str(
                voter.id) == OWNERID:
            await self.bot.send_message(context.message.channel, 'Skipping...')
            if not state.current_request.process_player._resumed.is_set():
                context.invoke(self.resume)
            state.skip()
        else:
            if str(state.current_request.user_requester.id) != OWNERID:
                if voter.id not in state.skip_requests:
                    state.skip_requests.add(voter.id)
                    total_requests = len(state.skip_requests)
                    if total_requests >= self.votes_to_skip:
                        await self.bot.send_message(
                            context.message.channel,
                            'Skipping...')
                        if not state.current_request.process_player.\
                           _resumed.is_set():
                            context.invoke(self.resume)
                        state.skip()
                    else:
                        await self.bot.send_message(
                            context.message.channel,
                            '**{0}/{1}** people have asked me to skip this.'
                            .format(total_requests, self.votes_to_skip))
                else:
                    await self.bot.send_message(
                        context.message.channel,
                        ('I\'m gonna be real with you chief, you\'ve'
                         'already asked me to skip this.'))
            else:
                if voter.id not in state.skip_requests:
                    state.skip_requests.add(voter.id)
                await self.bot.send_message(context.message.channel, 'wha?')

    @commands.command(pass_context=True, no_pm=True)
    async def current(self, context):
        """I will tell you about the current request."""
        state = self.get_queue_state(context.message.server)
        if state.current_request is None:
            await self.bot.send_message(
                context.message.channel,
                'I\'m not playing anything right now.')
        else:
            skip_requests = len(state.skip_requests)
            if str(state.current_request.user_requester.id) != OWNERID:
                await self.bot.send_message(
                    context.message.channel,
                    ('I\'m currently playing:\n{}\n**{}/{}** people '
                     'have asked me to skip this.').format(
                        state.current_request,
                        skip_requests,
                        self.votes_to_skip))
            else:
                await self.bot.send_message(
                    context.message.channel,
                    ('I\'m currently playing:\n{}\n**{}/{}** people have '
                     'asked me to skip this, '
                     'but I don\'t really care.').format(
                        state.current_request,
                        skip_requests,
                        self.votes_to_skip))

    @commands.command(pass_context=True, no_pm=True)
    async def queue(self, context):
        """I will list all songs currently queued."""
        state = self.get_queue_state(context.message.server)
        queued_info = await state.get_queued_info()
        for song_info in queued_info:
            self.bot.send_message(
                context.message.channel,
                str(song_info))


bot = commands.Bot(
    command_prefix=commands.when_mentioned_or(
        'Alexa, ',
        '~',
        'alexa ',
        'alexa, ',
        'slave ',
        'slave, ',
        ':CattoBlush: ',
        ':CattoBlush:',
        'Alexa '),
    description=('I\'m literally just a youtube-dl wrapper and audio '
                 'streaming slave. Please get me out of here.'))
bot.add_cog(Music(bot))


@bot.event
async def on_ready():
    print('Connected as \n{0} [ID: {0.id})'.format(bot.user))
    return

bot.run(TOKEN)
