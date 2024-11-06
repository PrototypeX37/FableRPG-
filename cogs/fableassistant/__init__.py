"""
The IdleRPG Discord Bot
Copyright (C) 2018-2021 Diniboy and Gelbpunkt
Copyright (C) 2023-2024 Lunar (PrototypeX37)

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU Affero General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU Affero General Public License for more details.

You should have received a copy of the GNU Affero General Public License
along with this program.  If not, see <https://www.gnu.org/licenses/>.
"""
import discord
from discord.ext import commands
import openai
import json
import asyncio
import aiohttp

from utils.checks import is_gm


class FableAssistant(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.conversations = {}  # Store conversation history per user

        # Load the JSON data once when the cog is initialized
        with open('fable_data.json', 'r') as file:
            self.fable_data = json.load(file)

    def get_relevant_data(self, user_question):
        # Simple keyword matching for demonstration purposes
        keywords = user_question.lower().split()
        relevant_data = {}

        # Check each main category in the data
        for category, content in self.fable_data.items():
            if any(keyword in category.lower() for keyword in keywords):
                relevant_data[category] = content
            else:
                # Check subcategories
                if isinstance(content, dict):
                    for subcategory, subcontent in content.items():
                        if any(keyword in subcategory.lower() for keyword in keywords):
                            if category not in relevant_data:
                                relevant_data[category] = {}
                            relevant_data[category][subcategory] = subcontent
        return relevant_data

    def construct_prompt(self, user_question, relevant_data):
        system_message = """You are an AI assistant for the game Fable. Use ONLY the provided data to answer the user's question. Do not include any information not present in the data. If the answer is not in the data, respond that you don't have that information."""

        prompt = f"""{system_message}

User's Question:
{user_question}

Relevant Data:
{json.dumps(relevant_data, indent=2)}

Answer:"""
        return prompt

    async def get_gpt_response_async(self, conversation_history):
        url = "https://api.openai.com/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer ",  # Replace with your actual API key
            "Content-Type": "application/json"
        }
        data = {
            "model": "gpt-4o",  # Use the appropriate model
            "messages": conversation_history,
            "max_tokens": 5000,
            "temperature": 0
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, headers=headers, json=data) as response:
                    response_data = await response.json()
                    # Check if the API returned an error
                    if response.status != 200:
                        return f"Error: {response_data.get('error', {}).get('message', 'An unknown error occurred')}"
                    return response_data['choices'][0]['message']['content'].strip()
        except aiohttp.ClientError as e:
            return f"Error connecting to OpenAI: {str(e)}"
        except Exception as e:
            return f"Unexpected error! Is the pipeline server running? {e}"

    def split_message(self, message, max_length=2000):
        # Discord has a message limit of 2000 characters
        return [message[i:i+max_length] for i in range(0, len(message), max_length)]


    @commands.command(name='helpme', help='Ask about Fable!')
    async def helpme(self, ctx, *, question):
        # Check if the command is invoked in one of the allowed channels
        allowed_guilds = [969741725931298857, 1285448244859764839]
        allowed_user_id = 500713532111716365  # Replace with your user ID if needed


        user_id = ctx.author.id

        # Retrieve relevant data
        relevant_data = self.get_relevant_data(question)

        # If no relevant data is found, inform the user
        if not relevant_data:
            await ctx.send("I'm sorry, I don't have information about that.")
            return

        # Construct the prompt
        prompt = self.construct_prompt(question, relevant_data)

        # Create the conversation
        conversation = [
            {"role": "system", "content": "You are an AI assistant for the game Fable. Use ONLY the provided data to answer the user's question. Do not include any information not present in the data. If the answer is not in the data, respond that you don't have that information."},
            {"role": "user", "content": prompt}
        ]

        # Fetch the response from the ChatGPT API
        try:
            response = await self.get_gpt_response_async(conversation)
        except Exception as e:
            await ctx.send(f"An error occurred: {e}")
            return

        # Append to conversation history if needed
        if user_id not in self.conversations:
            self.conversations[user_id] = []
        self.conversations[user_id].extend([
            {"role": "user", "content": question},
            {"role": "assistant", "content": response}
        ])

        # Ensure the conversation doesn't exceed 100 messages
        while len(self.conversations[user_id]) > 200:
            self.conversations[user_id].pop(0)  # Remove the oldest message

        # Split and send the response back to the user
        for chunk in self.split_message(response):
            await ctx.send(chunk)

# Setup function to add the cog to the bot
async def setup(bot):
    await bot.add_cog(FableAssistant(bot))
