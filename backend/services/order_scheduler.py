"""
Order scheduling service
Background task for periodically processing pending orders
"""

import asyncio
import threading
import time
import logging
from typing import Optional

from database.connection import SessionLocal
from .order_matching import process_all_pending_orders

logger = logging.getLogger(__name__)


class OrderScheduler:
    """Order scheduler"""
    
    def __init__(self, interval_seconds: int = 5):
        """
        Initialize the order scheduler

        Args:
            interval_seconds: Check interval (seconds)
        """
        self.interval_seconds = interval_seconds
        self.running = False
        self.thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._lifecycle_lock = threading.RLock()
    
    def start(self):
        """Start the scheduler"""
        with self._lifecycle_lock:
            if self.running:
                logger.warning("Order scheduler is already running")
                return
            if self.thread is not None and self.thread.is_alive():
                raise RuntimeError(
                    "order scheduler has a live thread from a failed start; stop it first"
                )

            candidate = threading.Thread(target=self._run_scheduler, daemon=True)
            self.running = True
            self._stop_event.clear()
            try:
                candidate.start()
                # Publish exactly one owned handle while start/stop are
                # serialized by the same lifecycle lock.
                self.thread = candidate
                logger.info(
                    "Order scheduler started, check interval: %s seconds",
                    self.interval_seconds,
                )
            except BaseException:
                self.running = False
                self._stop_event.set()
                if candidate.is_alive():
                    candidate.join(timeout=10)
                self.thread = candidate if candidate.is_alive() else None
                raise
    
    def stop(self):
        """Stop the scheduler"""
        with self._lifecycle_lock:
            thread_alive = bool(self.thread and self.thread.is_alive())
            if not self.running and not thread_alive:
                return

            self.running = False
            self._stop_event.set()

            if self.thread and self.thread.is_alive():
                self.thread.join(timeout=10)
            if self.thread and self.thread.is_alive():
                raise RuntimeError("order scheduler thread did not stop within 10 seconds")
            self.thread = None

            logger.info("Order scheduler stopped")

    def has_pending_cleanup(self) -> bool:
        with self._lifecycle_lock:
            return bool(
                self.running
                or (self.thread is not None and self.thread.is_alive())
            )
    
    def _run_scheduler(self):
        """Scheduler main loop"""
        logger.info("Order scheduler started running")
        
        while self.running and not self._stop_event.is_set():
            try:
                # Process orders
                self._process_orders()
                
                # Wait for next execution
                if self._stop_event.wait(timeout=self.interval_seconds):
                    break
                    
            except Exception as e:
                logger.error(f"Order scheduler execution error: {e}")
                # Wait briefly after error to avoid rapid looping
                time.sleep(1)
        
        logger.info("Order scheduler main loop ended")
    
    def _process_orders(self):
        """Process pending orders"""
        db = SessionLocal()
        try:
            executed_count, total_checked = process_all_pending_orders(db)
            
            if total_checked > 0:
                logger.debug(f"Order processing: checked {total_checked}, executed {executed_count}")
            
        except Exception as e:
            logger.error(f"Error processing orders: {e}")
        finally:
            db.close()
    
    def process_orders_once(self):
        """Manually execute order processing once"""
        if not self.running:
            logger.warning("Order scheduler not running, cannot process orders")
            return
        
        try:
            self._process_orders()
            logger.info("Manual order processing completed")
        except Exception as e:
            logger.error(f"Manual order processing failed: {e}")


# Global scheduler instance
order_scheduler = OrderScheduler(interval_seconds=5)


def start_order_scheduler():
    """Start global order scheduler"""
    order_scheduler.start()


def stop_order_scheduler():
    """Stop global order scheduler"""
    order_scheduler.stop()


def order_scheduler_has_pending_cleanup() -> bool:
    return order_scheduler.has_pending_cleanup()


def get_scheduler_status():
    """Get scheduler status"""
    return {
        "running": order_scheduler.running,
        "interval_seconds": order_scheduler.interval_seconds,
        "thread_alive": order_scheduler.thread.is_alive() if order_scheduler.thread else False
    }
