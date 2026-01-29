"""
Clean Pyrogram Account Manager
Only handles login and manual OTP fetching
"""

import logging
import re
import asyncio
from datetime import datetime
from pyrogram import Client
from pyrogram.errors import (
    PhoneNumberInvalid, PhoneCodeInvalid,
    PhoneCodeExpired, SessionPasswordNeeded,
    FloodWait, PhoneCodeEmpty
)

logger = logging.getLogger(__name__)

class AccountManager:
    def __init__(self, api_id, api_hash):
        self.api_id = api_id
        self.api_hash = api_hash
        self.clients = {}
    
    async def send_otp(self, phone_number):
        """Send OTP to phone number"""
        try:
            # Create new client for this login session
            client = Client(
                name=f"login_{int(datetime.now().timestamp())}",
                api_id=self.api_id,
                api_hash=self.api_hash,
                in_memory=True,
                no_updates=True
            )
            
            await client.connect()
            sent_code = await client.send_code(phone_number)
            
            # Store client for this session
            session_key = f"login_{phone_number}"
            self.clients[session_key] = client
            
            return {
                "success": True,
                "phone_code_hash": sent_code.phone_code_hash,
                "client_key": session_key
            }
            
        except FloodWait as e:
            return {
                "success": False,
                "error": f"Flood wait: {e.value} seconds"
            }
        except Exception as e:
            logger.error(f"Send OTP error: {e}")
            return {
                "success": False,
                "error": str(e)
            }
    
    async def verify_otp(self, client_key, otp_code, phone_number, phone_code_hash):
        """Verify OTP and get session string"""
        try:
            if client_key not in self.clients:
                return {
                    "success": False,
                    "error": "Session expired"
                }
            
            client = self.clients[client_key]
            
            try:
                await client.sign_in(
                    phone_number=phone_number,
                    phone_code=otp_code,
                    phone_code_hash=phone_code_hash
                )
                two_step_password = None
                has_2fa = False
                
            except SessionPasswordNeeded:
                return {
                    "success": False,
                    "needs_2fa": True
                }
            
            # Get session string
            session_string = await client.export_session_string()
            
            # Cleanup
            await client.disconnect()
            del self.clients[client_key]
            
            return {
                "success": True,
                "session_string": session_string,
                "has_2fa": has_2fa,
                "two_step_password": two_step_password
            }
            
        except PhoneCodeInvalid:
            return {
                "success": False,
                "error": "Invalid OTP code"
            }
        except PhoneCodeExpired:
            return {
                "success": False,
                "error": "OTP code expired"
            }
        except Exception as e:
            logger.error(f"Verify OTP error: {e}")
            return {
                "success": False,
                "error": str(e)
            }
    
    async def verify_2fa(self, client_key, password):
        """Verify 2FA password"""
        try:
            if client_key not in self.clients:
                return {
                    "success": False,
                    "error": "Session expired"
                }
            
            client = self.clients[client_key]
            
            await client.check_password(password)
            
            # Get session string
            session_string = await client.export_session_string()
            
            # Cleanup
            await client.disconnect()
            del self.clients[client_key]
            
            return {
                "success": True,
                "session_string": session_string,
                "has_2fa": True,
                "two_step_password": password
            }
            
        except Exception as e:
            logger.error(f"2FA verification error: {e}")
            return {
                "success": False,
                "error": str(e)
            }
    
    async def get_latest_otp(self, session_string, phone):
        """Manually fetch latest OTP from Telegram messages"""
        client = None
        try:
            # Create client from session string
            client = Client(
                name=f"otp_fetch_{int(datetime.now().timestamp())}",
                session_string=session_string,
                api_id=self.api_id,
                api_hash=self.api_hash,
                in_memory=True,
                no_updates=True
            )
            
            await client.connect()
            
            latest_otp = None
            latest_time = None
            
            # Search in Telegram chat (where OTPs come)
            async for message in client.get_chat_history("Telegram", limit=20):
                if message.text:
                    # Look for 5-digit OTP codes
                    otp_matches = re.findall(r'\b\d{5}\b', message.text)
                    if otp_matches:
                        message_time = message.date.timestamp() if message.date else 0
                        
                        # Get the most recent OTP
                        if latest_time is None or message_time > latest_time:
                            latest_time = message_time
                            latest_otp = otp_matches[0]
            
            # Also check from 777000 (Telegram notifications)
            if not latest_otp:
                async for message in client.get_chat_history(777000, limit=20):
                    if message.text:
                        otp_matches = re.findall(r'\b\d{5}\b', message.text)
                        if otp_matches:
                            message_time = message.date.timestamp() if message.date else 0
                            if latest_time is None or message_time > latest_time:
                                latest_time = message_time
                                latest_otp = otp_matches[0]
            
            await client.disconnect()
            
            return latest_otp
            
        except Exception as e:
            logger.error(f"Get OTP error: {e}")
            if client:
                try:
                    await client.disconnect()
                except:
                    pass
            return None
    
    def run_async(self, coro):
        """Run async function in sync context"""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            return loop.run_until_complete(coro)
        finally:
            loop.close()
