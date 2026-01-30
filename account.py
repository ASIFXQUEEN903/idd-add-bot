"""
Simplified Pyrogram Account Manager with Synchronous Wrapper
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
        self.login_sessions = {}
    
    def _run_sync(self, coro):
        """Run async function synchronously"""
        # Create a new event loop for each call to avoid conflicts
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            return loop.run_until_complete(coro)
        finally:
            loop.close()
            asyncio.set_event_loop(None)
    
    def send_otp(self, phone_number):
        """Send OTP to phone number"""
        try:
            return self._run_sync(self._send_otp_async(phone_number))
        except Exception as e:
            logger.error(f"Send OTP error: {e}")
            return {
                "success": False,
                "error": f"Failed to send OTP: {str(e)}"
            }
    
    async def _send_otp_async(self, phone_number):
        """Async function to send OTP"""
        client = None
        try:
            client_name = f"login_{int(datetime.now().timestamp())}"
            client = Client(
                name=client_name,
                api_id=self.api_id,
                api_hash=self.api_hash,
                in_memory=True,
                no_updates=True
            )
            
            await client.connect()
            sent_code = await client.send_code(phone_number)
            
            session_key = f"{phone_number}_{int(datetime.now().timestamp())}"
            self.login_sessions[session_key] = client
            
            return {
                "success": True,
                "phone_code_hash": sent_code.phone_code_hash,
                "session_key": session_key
            }
        except Exception as e:
            if client:
                try:
                    await client.disconnect()
                except:
                    pass
            raise e
    
    def verify_otp(self, session_key, otp_code, phone_number, phone_code_hash):
        """Verify OTP and get session string"""
        try:
            return self._run_sync(
                self._verify_otp_async(session_key, otp_code, phone_number, phone_code_hash)
            )
        except Exception as e:
            logger.error(f"Verify OTP error: {e}")
            return {
                "success": False,
                "error": f"OTP verification failed: {str(e)}"
            }
    
    async def _verify_otp_async(self, session_key, otp_code, phone_number, phone_code_hash):
        """Async function to verify OTP"""
        client = self.login_sessions.get(session_key)
        if not client:
            return {
                "success": False,
                "error": "Session expired. Please start again."
            }
        
        try:
            await client.sign_in(
                phone_number=phone_number,
                phone_code=otp_code,
                phone_code_hash=phone_code_hash
            )
        except SessionPasswordNeeded:
            return {
                "success": False,
                "needs_2fa": True,
                "session_key": session_key
            }
        except Exception as e:
            await client.disconnect()
            del self.login_sessions[session_key]
            raise e
        
        session_string = await client.export_session_string()
        await client.disconnect()
        
        if session_key in self.login_sessions:
            del self.login_sessions[session_key]
        
        return {
            "success": True,
            "session_string": session_string,
            "has_2fa": False,
            "two_step_password": None
        }
    
    def verify_2fa(self, session_key, password):
        """Verify 2FA password"""
        try:
            return self._run_sync(self._verify_2fa_async(session_key, password))
        except Exception as e:
            logger.error(f"Verify 2FA error: {e}")
            return {
                "success": False,
                "error": f"2FA verification failed: {str(e)}"
            }
    
    async def _verify_2fa_async(self, session_key, password):
        """Async function to verify 2FA"""
        client = self.login_sessions.get(session_key)
        if not client:
            return {
                "success": False,
                "error": "Session expired. Please start again."
            }
        
        try:
            await client.check_password(password)
        except Exception as e:
            await client.disconnect()
            del self.login_sessions[session_key]
            raise e
        
        session_string = await client.export_session_string()
        await client.disconnect()
        del self.login_sessions[session_key]
        
        return {
            "success": True,
            "session_string": session_string,
            "has_2fa": True,
            "two_step_password": password
        }
    
    def get_latest_otp(self, session_string, phone):
        """Manually fetch latest OTP from Telegram messages"""
        try:
            return self._run_sync(self._get_latest_otp_async(session_string, phone))
        except Exception as e:
            logger.error(f"Get OTP error: {e}")
            return None
    
    async def _get_latest_otp_async(self, session_string, phone):
        """Async function to fetch OTP"""
        client = None
        try:
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
            
            # Search in recent messages
            try:
                async for message in client.get_chat_history("Telegram", limit=20):
                    if message.text:
                        otp_matches = re.findall(r'\b\d{5}\b', message.text)
                        if otp_matches:
                            message_time = message.date.timestamp() if message.date else 0
                            if latest_time is None or message_time > latest_time:
                                latest_time = message_time
                                latest_otp = otp_matches[0]
            except:
                pass
            
            await client.disconnect()
            return latest_otp
            
        except Exception as e:
            if client:
                try:
                    await client.disconnect()
                except:
                    pass
            raise e
