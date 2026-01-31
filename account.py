"""
Clean Pyrogram Account Manager with Fixed Async Handling
"""

import logging
import re
import asyncio
import threading
from datetime import datetime
from pyrogram import Client
from pyrogram.errors import (
    PhoneNumberInvalid, PhoneCodeInvalid,
    PhoneCodeExpired, SessionPasswordNeeded,
    FloodWait, PhoneCodeEmpty
)

logger = logging.getLogger(__name__)

# Global event loop manager
class AsyncManager:
    def __init__(self):
        self.lock = threading.Lock()
    
    def run_async(self, coro):
        """Run async function in sync context with proper event loop handling"""
        try:
            # Try to get existing event loop
            try:
                loop = asyncio.get_event_loop()
            except RuntimeError:
                # Create new event loop if none exists
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
            
            # Check if loop is running
            if loop.is_running():
                # Run in separate thread with new event loop
                return self._run_in_thread(coro)
            else:
                # Run in current event loop
                return loop.run_until_complete(coro)
        except Exception as e:
            logger.error(f"Async operation failed: {e}")
            raise
    
    def _run_in_thread(self, coro):
        """Run coroutine in separate thread"""
        result = None
        exception = None
        
        def run():
            nonlocal result, exception
            try:
                # Create new event loop for this thread
                new_loop = asyncio.new_event_loop()
                asyncio.set_event_loop(new_loop)
                result = new_loop.run_until_complete(coro)
                new_loop.close()
            except Exception as e:
                exception = e
        
        thread = threading.Thread(target=run)
        thread.start()
        thread.join()
        
        if exception:
            raise exception
        return result


class AccountManager:
    def __init__(self, api_id, api_hash):
        self.api_id = api_id
        self.api_hash = api_hash
        self.async_manager = AsyncManager()
        self.login_sessions = {}  # {session_key: client}
    
    def send_otp(self, phone_number):
        """Send OTP to phone number"""
        return self.async_manager.run_async(self._send_otp_async(phone_number))
    
    async def _send_otp_async(self, phone_number):
        """Async function to send OTP"""
        try:
            # Create client with unique name
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
            
            # Store client for this session
            session_key = f"{phone_number}_{int(datetime.now().timestamp())}"
            self.login_sessions[session_key] = client
            
            return {
                "success": True,
                "phone_code_hash": sent_code.phone_code_hash,
                "session_key": session_key
            }
            
        except FloodWait as e:
            return {
                "success": False,
                "error": f"Please wait {e.value} seconds before trying again"
            }
        except Exception as e:
            logger.error(f"Send OTP error: {e}")
            return {
                "success": False,
                "error": str(e)
            }
    
    def verify_otp(self, session_key, otp_code, phone_number, phone_code_hash):
        """Verify OTP and get session string"""
        return self.async_manager.run_async(
            self._verify_otp_async(session_key, otp_code, phone_number, phone_code_hash)
        )
    
    async def _verify_otp_async(self, session_key, otp_code, phone_number, phone_code_hash):
        """Async function to verify OTP"""
        try:
            if session_key not in self.login_sessions:
                return {
                    "success": False,
                    "error": "Session expired. Please start again."
                }
            
            client = self.login_sessions[session_key]
            
            try:
                await client.sign_in(
                    phone_number=phone_number,
                    phone_code=otp_code,
                    phone_code_hash=phone_code_hash
                )
                has_2fa = False
                two_step_password = None
                
            except SessionPasswordNeeded:
                return {
                    "success": False,
                    "needs_2fa": True,
                    "session_key": session_key
                }
            
            except Exception as e:
                return {
                    "success": False,
                    "error": f"OTP verification failed: {str(e)}"
                }
            
            # Get session string
            session_string = await client.export_session_string()
            
            # Cleanup
            await self._safe_disconnect(client)
            del self.login_sessions[session_key]
            
            return {
                "success": True,
                "session_string": session_string,
                "has_2fa": has_2fa,
                "two_step_password": two_step_password
            }
            
        except Exception as e:
            logger.error(f"Verify OTP error: {e}")
            
            # Cleanup on error
            if session_key in self.login_sessions:
                client = self.login_sessions[session_key]
                await self._safe_disconnect(client)
                del self.login_sessions[session_key]
            
            return {
                "success": False,
                "error": str(e)
            }
    
    def verify_2fa(self, session_key, password):
        """Verify 2FA password"""
        return self.async_manager.run_async(
            self._verify_2fa_async(session_key, password)
        )
    
    async def _verify_2fa_async(self, session_key, password):
        """Async function to verify 2FA"""
        try:
            if session_key not in self.login_sessions:
                return {
                    "success": False,
                    "error": "Session expired. Please start again."
                }
            
            client = self.login_sessions[session_key]
            
            await client.check_password(password)
            
            # Get session string
            session_string = await client.export_session_string()
            
            # Cleanup
            await self._safe_disconnect(client)
            del self.login_sessions[session_key]
            
            return {
                "success": True,
                "session_string": session_string,
                "has_2fa": True,
                "two_step_password": password
            }
            
        except Exception as e:
            logger.error(f"2FA verification error: {e}")
            
            # Cleanup on error
            if session_key in self.login_sessions:
                client = self.login_sessions[session_key]
                await self._safe_disconnect(client)
                del self.login_sessions[session_key]
            
            return {
                "success": False,
                "error": str(e)
            }
    
    def get_latest_otp(self, session_string, phone):
        """Manually fetch latest OTP from Telegram messages"""
        return self.async_manager.run_async(
            self._get_latest_otp_async(session_string, phone)
        )
    
    async def _get_latest_otp_async(self, session_string, phone):
        """Async function to fetch OTP"""
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
            
            # Search in Telegram chat
            try:
                async for message in client.get_chat_history("Telegram", limit=20):
                    if message.text and "code" in message.text.lower():
                        otp_matches = re.findall(r'\b\d{5}\b', message.text)
                        if otp_matches:
                            message_time = message.date.timestamp() if message.date else 0
                            if latest_time is None or message_time > latest_time:
                                latest_time = message_time
                                latest_otp = otp_matches[0]
            except:
                pass
            
            # Search in 777000
            if not latest_otp:
                try:
                    async for message in client.get_chat_history(777000, limit=20):
                        if message.text and "code" in message.text.lower():
                            otp_matches = re.findall(r'\b\d{5}\b', message.text)
                            if otp_matches:
                                message_time = message.date.timestamp() if message.date else 0
                                if latest_time is None or message_time > latest_time:
                                    latest_time = message_time
                                    latest_otp = otp_matches[0]
                except:
                    pass
            
            await self._safe_disconnect(client)
            return latest_otp
            
        except Exception as e:
            logger.error(f"Get OTP error: {e}")
            if client:
                await self._safe_disconnect(client)
            return None
    
    async def _safe_disconnect(self, client):
        """Safely disconnect client"""
        try:
            if client and hasattr(client, 'is_connected') and client.is_connected:
                await client.disconnect()
        except:
            pass
    
    def cleanup_sessions(self):
        """Cleanup old sessions"""
        try:
            for session_key in list(self.login_sessions.keys()):
                # Remove sessions older than 30 minutes
                try:
                    timestamp = int(session_key.split('_')[-1])
                    if datetime.now().timestamp() - timestamp > 1800:  # 30 minutes
                        client = self.login_sessions.pop(session_key, None)
                        if client:
                            asyncio.run(self._safe_disconnect(client))
                except:
                    continue
        except Exception as e:
            logger.error(f"Cleanup error: {e}")
