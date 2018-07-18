# Music Slave for discord
An Discord bot that plays music using the `youtube-dl` module for python.

## How to set this up:
Note that this app requires `python3.6` or greater due to reliance on asynchronous routine features.
### 1. Install the discord.py dependency for `python`
``
python3 -m pip install discord.py[voice]
``
### 2. Add your API keys
For information on how to generate a Discord app and API keys, consult the following:

https://discordapp.com/developers/docs/intro

Once you have your API keys, put them in a file called
``
credentials.py
``
in the root of the project with the following contents:
```
TOKEN = '<your-app-token>'
KEY = '<your-user-key>'

BOTID = '<your-app-id>'
OWNERID = '<your-user-id>'
```
### 3. Invite it to a server
For the app to function, it must first be invited to a server. This is done by generating an OAuth2 URL using the Discord app tools. 

### 4. Run it
In the root of the project, use the command
``
python3 music_slave.py
``
