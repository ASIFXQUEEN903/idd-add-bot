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

# Thread-safe async manager
class AsyncManager:
    def __init__(self):
        self.lock = threading.Lock()
        self._thread_local = threading.local()
    
    def get_or_create_loop(self):
        """Get or create event loop for current thread"""
        if not hasattr(self._thread_local, "loop"):
            self._thread_local.loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._thread_local.loop)
        return self._thread_local.loop
    
    def run_async(self, coro):
        """Run async function in sync context"""
        with self.lock:
            loop = self.get_or_create_loop()
            if loop.is_running():
                # If loop is already running, run in separate thread
                return self._run_in_new_thread(coro)
            else:
                # Run in current loop
                try:
                    return loop.run_until_complete(coro)
                except Exception as e:
                    logger.error(f"Async error: {e}")
                    raise
    
    def _run_in_new_thread(self, coro):
        """Run coroutine in new thread with its own event loop"""
        result = None
        exception = None
        event = threading.Event()
        
        def runner():
            nonlocal result, exception
            try:
                # Create new event loop for this thread
                new_loop = asyncio.new_event_loop()
                asyncio.set_event_loop(new_loop)
                try:
                    result = new_loop.run_until_complete(coro)
                finally:
                    new_loop.close()
            except Exception as e:
                exception = e
            finally:
                event.set()
        
        thread = threading.Thread(target=runner)
        thread.start()
        thread.join()
        event.wait()
        
        if exception:
            raise exception
        return result
    
    def cleanup(self):
        """Clean up event loops"""
        try:
            if hasattr(self._thread_local, "loop"):
                self._thread_local.loop.close()
                del self._thread_local.loop
        except:
            pass


class AccountManager:
    def __init__(self, api_id, api_hash):
        self.api_id = api_id
        self.api_hash = api_hash
        self.async_manager = AsyncManager()
        self.login_sessions = {}  # {session_key: client}
    
    def send_otp(self, phone_number):
        """Send OTP to phone number"""
        try:
            result = self.async_manager.run_async(self._send_otp_async(phone_number))
            return result
        except Exception as e:
            logger.error(f"Send OTP error in manager: {e}")
            return {
                "success": False,
                "error": f"Failed to send OTP: {str(e)}"
            }
    
    async def _send_otp_async(self, phone_number):
        """Async function to send OTP"""
        client = None
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
            
            logger.info(f"OTP sent to {phone_number}, session_key: {session_key}")
            
            return {
                "success": True,
                "phone_code_hash": sent_code.phone_code_hash,
                "session_key": session_key
            }
            
        except FloodWait as e:
            logger.warning(f"Flood wait for {phone_number}: {e.value} seconds")
            return {
                "success": False,
                "error": f"Please wait {e.value} seconds before trying again"
            }
        except Exception as e:
            logger.error(f"Send OTP async error: {e}")
            if client:
                await self._safe_disconnect(client)
            return {
                "success": False,
                "error": str(e)
            }
    
    def verify_otp(self, session_key, otp_code, phone_number, phone_code_hash):
        """Verify OTP and get session string"""
        try:
            result = self.async_manager.run_async(
                self._verify_otp_async(session_key, otp_code, phone_number, phone_code_hash)
            )
            return result
        except Exception as e:
            logger.error(f"Verify OTP error in manager: {e}")
            return {
                "success": False,
                "error": f"OTP verification failed: {str(e)}"
            }
    
    async def _verify_otp_async(self, session_key, otp_code, phone_number, phone_code_hash):
        """Async function to verify OTP"""
        client = None
        try:
            if session_key not in self.login_sessions:
                logger.warning(f"Session key not found: {session_key}")
                return {
                    "success": False,
                    "error": "Session expired. Please start again."
                }
            
            client = self.login_sessions[session_key]
            logger.info(f"Verifying OTP for session: {session_key}")
            
            try:
                # Try to sign in with OTP
                signed_in = await client.sign_in(
                    phone_number=phone_number,
                    phone_code=otp_code,
                    phone_code_hash=phone_code_hash
                )
                logger.info(f"Successfully signed in for {phone_number}")
                has_2fa = False
                two_step_password = None
                
            except SessionPasswordNeeded:
                logger.info(f"2FA required for {phone_number}")
                # Keep client in session for 2FA
                return {
                    "success": False,
                    "needs_2fa": True,
                    "session_key": session_key
                }
            
            except Exception as e:
                logger.error(f"Sign in error for {phone_number}: {e}")
                # Cleanup on error
                if session_key in self.login_sessions:
                    await self._safe_disconnect(self.login_sessions[session_key])
                    del self.login_sessions[session_key]
                return {
                    "success": False,
                    "error": f"OTP verification failed: {str(e)}"
                }
            
            # Get session string
            session_string = await client.export_session_string()
            logger.info(f"Got session string for {phone_number}")
            
            # Cleanup
            await self._safe_disconnect(client)
            if session_key in self.login_sessions:
                del self.login_sessions[session_key]
            
            return {
                "success": True,
                "session_string": session_string,
                "has_2fa": has_2fa,
                "two_step_password": two_step_password
            }
            
        except Exception as e:
            logger.error(f"Verify OTP async error: {e}")
            
            # Cleanup on error
            if client:
                await self._safe_disconnect(client)
            if session_key in self.login_sessions:
                del self.login_sessions[session_key]
            
            return {
                "success": False,
                "error": str(e)
            }
    
    def verify_2fa(self, session_key, password):
        """Verify 2FA password"""
        try:
            result = self.async_manager.run_async(
                self._verify_2fa_async(session_key, password)
            )
            return result
        except Exception as e:
            logger.error(f"Verify 2FA error in manager: {e}")
            return {
                "success": False,
                "error": f"2FA verification failed: {str(e)}"
            }
    
    async def _verify_2fa_async(self, session_key, password):
        """Async function to verify 2FA"""
        client = None
        try:
            if session_key not in self.login_sessions:
                logger.warning(f"Session key not found for 2FA: {session_key}")
                return {
                    "success": False,
                    "error": "Session expired. Please start again."
                }
            
            client = self.login_sessions[session_key]
            logger.info(f"Verifying 2FA for session: {session_key}")
            
            try:
                await client.check_password(password)
                logger.info(f"2FA successful for session: {session_key}")
            except Exception as e:
                logger.error(f"2FA check error: {e}")
                # Cleanup on error
                await self._safe_disconnect(client)
                del self.login_sessions[session_key]
                return {
                    "success": False,
                    "error": f"2FA verification failed: {str(e)}"
                }
            
            # Get session string
            session_string = await client.export_session_string()
            logger.info(f"Got session string after 2FA for session: {session_key}")
            
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
            logger.error(f"2FA verification async error: {e}")
            
            # Cleanup on error
            if client:
                await self._safe_disconnect(client)
            if session_key in self.login_sessions:
                del self.login_sessions[session_key]
            
            return {
                "success": False,
                "error": str(e)
            }
    
    def get_latest_otp(self, session_string, phone):
        """Manually fetch latest OTP from Telegram messages"""
        try:
            result = self.async_manager.run_async(
                self._get_latest_otp_async(session_string, phone)
            )
            return result
        except Exception as e:
            logger.error(f"Get OTP error in manager: {e}")
            return None
    
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
            logger.info(f"Connected for OTP fetch for {phone}")
            
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
                                logger.debug(f"Found OTP in Telegram: {latest_otp}")
            except Exception as e:
                logger.warning(f"Search in Telegram chat failed: {e}")
            
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
                                    logger.debug(f"Found OTP in 777000: {latest_otp}")
                except Exception as e:
                    logger.warning(f"Search in 777000 failed: {e}")
            
            await self._safe_disconnect(client)
            
            if latest_otp:
                logger.info(f"Found OTP for {phone}: {latest_otp}")
            else:
                logger.info(f"No OTP found for {phone}")
                
            return latest_otp
            
        except Exception as e:
            logger.error(f"Get OTP async error: {e}")
            if client:
                await self._safe_disconnect(client)
            return None
    
    async def _safe_disconnect(self, client):
        """Safely disconnect client"""
        try:
            if client and hasattr(client, 'is_connected'):
                await client.disconnect()
                logger.debug(f"Client disconnected")
        except Exception as e:
            logger.warning(f"Safe disconnect error: {e}")
    
    def cleanup_sessions(self):
        """Cleanup old sessions"""
        try:
            current_time = datetime.now().timestamp()
            to_remove = []
            
            for session_key, client in list(self.login_sessions.items()):
                try:
                    timestamp = int(session_key.split('_')[-1])
                    if current_time - timestamp > 1800:  # 30 minutes
                        to_remove.append((session_key, client))
                except:
                    continue
            
            # Remove old sessions
            for session_key, client in to_remove:
                if session_key in self.login_sessions:
                    del self.login_sessions[session_key]
                # Run disconnect in background thread
                threading.Thread(
                    target=lambda: self.async_manager.run_async(
                        self._safe_disconnect(client)
                    )
                ).start()
                
            if to_remove:
                logger.info(f"Cleaned up {len(to_remove)} old sessions")
                
        except Exception as e:
            logger.error(f"Cleanup sessions error: {e}")
    
    def cleanup_all(self):
        """Cleanup all resources"""
        try:
            self.cleanup_sessions()
            self.async_manager.cleanup()
        except Exception as e:
            logger.error(f"Cleanup all error: {e}")
