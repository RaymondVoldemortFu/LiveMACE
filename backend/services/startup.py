"""Application startup initialization service"""

import logging
import threading
import anyio
import dotenv

from services.auto_trader import (
    place_ai_driven_crypto_order,
    place_random_crypto_order,
    AUTO_TRADE_JOB_ID,
    AI_TRADE_JOB_ID
)
from services.scheduler import start_scheduler, setup_market_tasks, task_scheduler, start_margin_monitor, start_database_snapshot
from services.container_service import ContainerService
from database.connection import SessionLocal

logger = logging.getLogger(__name__)

# Load environment variables from a .env file (searched from current working dir upwards).
# This keeps local development configuration in one place without overriding real env vars
# (e.g. Docker/K8s injected values).
dotenv.load_dotenv(dotenv_path=dotenv.find_dotenv(usecwd=True), override=False)


def initialize_services():
    """Initialize all services"""
    try:
        # Initialize Docker Container Service
        try:
            ContainerService()
            logger.info("Docker Container Service initialized successfully")
        except Exception as e:
            logger.error(f"Failed to initialize Docker Container Service: {e}")

        # Start the scheduler
        start_scheduler()
        logger.info("Scheduler service started")
        
        # Set up market-related scheduled tasks
        setup_market_tasks()
        logger.info("Market scheduled tasks have been set up")

        # Start automatic cryptocurrency trading task via reset to ensure market data & proper job ID
        from services.scheduler import reset_auto_trading_job
        try:
            reset_auto_trading_job()
            logger.info("Automatic cryptocurrency trading task reset initiated in background")
        except Exception as e:
            logger.error(f"Failed to initiate AI auto trading task: {e}")
            # Fallback to random trading schedule to keep demo functional
            try:
                schedule_auto_trading(interval_seconds=300, use_ai=False)
                jobs = task_scheduler.get_job_info()
                logger.warning(f"Falling back to random trading schedule. Jobs: {jobs}")
            except Exception as e2:
                logger.error(f"Failed to schedule fallback random trading task: {e2}")
        
        # Add price cache cleanup task (every 2 minutes)
        from services.price_cache import clear_expired_prices
        task_scheduler.add_interval_task(
            task_func=clear_expired_prices,
            interval_seconds=120,  # Clean every 2 minutes
            task_id="price_cache_cleanup"
        )
        logger.info("Price cache cleanup task started (2-minute interval)")
        
        # Start margin monitoring for leveraged positions (every 5 seconds)
        start_margin_monitor(interval_seconds=5)
        logger.info("Margin monitor started (5-second interval)")
        
        # Initialize asset metadata
        try:
            from init_asset_metadata import init_asset_metadata
            db = SessionLocal()
            try:
                init_asset_metadata(db)
                logger.info("Asset metadata initialized successfully")
            finally:
                db.close()
        except Exception as e:
            logger.error(f"Failed to initialize asset metadata: {e}")
        
        # Start database snapshot task for evaluation metrics (every 1 hour)
        start_database_snapshot(interval_seconds=3600)
        logger.info("Database snapshot started (1-hour interval)")
        
        logger.info("All services initialized successfully")
        
    except Exception as e:
        logger.error(f"Service initialization failed: {e}")
        raise


def shutdown_services():
    """Shut down all services"""
    try:
        from services.scheduler import stop_scheduler
        stop_scheduler()

        try:
            from services.order_scheduler import stop_order_scheduler

            stop_order_scheduler()
            logger.info("Order scheduler stopped")
        except Exception as e:
            logger.error(f"Failed to stop order scheduler: {e}")
        
        # Shutdown Docker Container Service
        try:
            ContainerService().shutdown()
            logger.info("Docker Container Service shutdown successfully")
        except Exception as e:
            logger.error(f"Failed to shutdown Docker Container Service: {e}")
            
        logger.info("All services have been shut down")
        
    except Exception as e:
        logger.error(f"Failed to shut down services: {e}")


async def startup_event():
    """FastAPI application startup event"""
    await anyio.to_thread.run_sync(initialize_services)


async def shutdown_event():
    """FastAPI application shutdown event"""
    await anyio.to_thread.run_sync(shutdown_services)


def schedule_auto_trading(interval_seconds: int = 300, max_ratio: float = 0.2, use_ai: bool = True) -> None:
    """Schedule automatic trading tasks
    
    Args:
        interval_seconds: Interval between trading attempts
        max_ratio: Maximum portion of portfolio to use per trade
        use_ai: If True, use AI-driven trading; if False, use random trading
    """
    from services.auto_trader import (
        place_ai_driven_crypto_order,
        place_random_crypto_order,
        AUTO_TRADE_JOB_ID,
        AI_TRADE_JOB_ID
    )

    def execute_trade():
        try:
            if use_ai:
                place_ai_driven_crypto_order(max_ratio)
            else:
                place_random_crypto_order(max_ratio)
            logger.info("Initial auto-trading execution completed")
        except Exception as e:
            logger.error(f"Error during initial auto-trading execution: {e}")

    if use_ai:
        task_func = place_ai_driven_crypto_order
        job_id = AI_TRADE_JOB_ID
        logger.info("Scheduling AI-driven crypto trading")
    else:
        task_func = place_random_crypto_order
        job_id = AUTO_TRADE_JOB_ID
        logger.info("Scheduling random crypto trading")

    # Schedule the recurring task
    task_scheduler.add_interval_task(
        task_func=task_func,
        interval_seconds=interval_seconds,
        task_id=job_id,
        max_ratio=max_ratio,
    )
    
    # Execute the first trade immediately in a separate thread to avoid blocking
    initial_trade = threading.Thread(target=execute_trade, daemon=True)
    initial_trade.start()
