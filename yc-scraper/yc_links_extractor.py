import argparse
import json
import logging
import re
from datetime import datetime, timedelta
from pathlib import Path
from time import sleep
from typing import List, Generator, Optional, Dict, Any

from selenium.webdriver import Firefox
from selenium.webdriver.common.by import By
from selenium.webdriver.firefox.options import Options
from selenium.common.exceptions import (
    NoSuchElementException,
    TimeoutException,
    WebDriverException
)
from tqdm import tqdm

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

# Cache and checkpoint file paths
CACHE_FILE = Path('scrapy-project/ycombinator/url_cache.json')
CHECKPOINT_FILE = Path('scrapy-project/ycombinator/scrape_checkpoint.json')
START_URLS_FILE = Path('scrapy-project/ycombinator/start_urls.txt')
CACHE_DURATION_DAYS = 30  # Cache is valid for 30 days


def make_driver(headless: bool = True) -> Optional[Firefox]:
    """Creates headless Firefox WebDriver instance.
    
    Args:
        headless: Whether to run in headless mode
        
    Returns:
        Firefox WebDriver instance or None if creation fails
    """
    try:
        firefox_options = Options()
        if headless:
            firefox_options.add_argument('-headless')
        driver = Firefox(options=firefox_options)
        logger.info("Firefox WebDriver created successfully")
        return driver
    except WebDriverException as e:
        logger.error(f"Failed to create Firefox WebDriver: {e}")
        logger.error("Make sure Firefox and geckodriver are installed")
        return None


page = "https://www.ycombinator.com/companies"


def get_page_source(driver: Firefox) -> bool:
    """Returns the source of the current page.
    
    Returns:
        True if page loaded successfully, False otherwise
    """
    try:
        driver.get(page)
        logger.info(f"Loaded page: {page}")
        return True
    except (TimeoutException, WebDriverException) as e:
        logger.error(f"Failed to load page {page}: {e}")
        return False


def click_see_all_options(driver: Firefox) -> bool:
    """Clicks 'See all options' button to load checkboxes for all batches.
    
    Returns:
        True if button clicked successfully, False otherwise
    """
    try:
        sleep(3)
        see_all_options = driver.find_element(By.LINK_TEXT, 'See all options')
        see_all_options.click()
        logger.info("Clicked 'See all options' button")
        return True
    except NoSuchElementException:
        logger.error("'See all options' button not found on page")
        return False
    except Exception as e:
        logger.error(f"Failed to click 'See all options': {e}")
        return False


def compile_batches(driver: Firefox, filter_batches: Optional[List[str]] = None, recent: Optional[int] = None) -> Generator:
    """Returns elements of checkboxes from all batches, optionally filtered.
    
    Args:
        driver: Firefox WebDriver instance
        filter_batches: List of specific batch names to include (e.g., ['W24', 'S24'])
        recent: Number of most recent batches to include
    
    Yields:
        WebElement objects representing batch checkboxes
    """
    # Match labels like 'Summer 2025', 'Winter 2024', etc.
    pattern = re.compile(r'^(Summer|Winter|Spring|Fall) \d{4}')
    
    try:
        bx = driver.find_elements(By.XPATH, '//label')
        logger.debug(f"Found {len(bx)} label elements for batches")
        
        matched = [element for element in bx if pattern.match(element.text)]
        logger.info(f"Matched {len(matched)} batch elements")
        
        # Filter batches if requested
        if filter_batches:
            # Convert full names to short codes for comparison
            filtered = []
            for element in matched:
                batch_name = element.text
                # Extract season initial and year (e.g., "Summer 2024" -> "S24")
                match = pattern.match(batch_name)
                if match:
                    season = batch_name.split()[0][0]  # First letter of season
                    year = batch_name.split()[1][-2:]  # Last 2 digits of year
                    batch_code = f"{season}{year}"
                    if batch_code in filter_batches or batch_name in filter_batches:
                        filtered.append(element)
            matched = filtered
            logger.info(f"Filtered to {len(matched)} batches: {filter_batches}")
        
        # Take only recent batches if requested
        if recent and recent < len(matched):
            matched = matched[:recent]
            logger.info(f"Limited to {recent} most recent batches")
        
        for element in matched:
            yield element
    except Exception as e:
        logger.error(f"Error compiling batches: {e}")
        return


def scroll_to_bottom(driver: Firefox) -> None:
    """Scrolls to the bottom of the page.
    
    Args:
        driver: Firefox WebDriver instance
    """

    # get scroll height
    last_height = driver.execute_script("return document.body.scrollHeight")

    while True:
        # scroll down to bottom
        driver.execute_script(
            "window.scrollTo(0, document.body.scrollHeight);")

        # wait to load page
        sleep(3)

        # calculate new scroll height and compare with last scroll height
        new_height = driver.execute_script("return document.body.scrollHeight")
        if new_height == last_height:
            break
        last_height = new_height


def fetch_url_paths(driver: Firefox) -> Generator[str, None, None]:
    """Returns a generator with url paths for all companies.
    
    Yields:
        Company URLs as strings
    """
    try:
        # contains 'companies' but not 'founders'
        elements = driver.find_elements(
            By.XPATH, ('//a[contains(@href,"/companies/") and not(contains(@href,"founders"))]'))
        for url in elements:
            href = url.get_attribute('href')
            if href:
                yield href
    except Exception as e:
        logger.error(f"Error fetching URL paths: {e}")
        return


def load_cache() -> Optional[Dict[str, Any]]:
    """Load cached URLs if still valid.
    
    Returns:
        Cached data dict or None if cache is invalid/missing
    """
    if not CACHE_FILE.exists():
        logger.info("No cache file found")
        return None
    
    try:
        with open(CACHE_FILE, 'r') as f:
            cache = json.load(f)
        
        cache_date = datetime.fromisoformat(cache['timestamp'])
        cache_age = datetime.now() - cache_date
        
        if cache_age < timedelta(days=CACHE_DURATION_DAYS):
            logger.info(f"Using cached URLs from {cache['timestamp']} ({cache['count']} URLs, {cache_age.days} days old)")
            return cache
        else:
            logger.info(f"Cache expired (age: {cache_age.days} days)")
            return None
    except (json.JSONDecodeError, KeyError, ValueError) as e:
        logger.error(f"Failed to load cache: {e}")
        return None


def save_cache(urls: List[str]) -> bool:
    """Save URLs to cache with timestamp.
    
    Args:
        urls: List of company URLs to cache
        
    Returns:
        True if save successful, False otherwise
    """
    try:
        cache = {
            'timestamp': datetime.now().isoformat(),
            'urls': urls,
            'count': len(urls),
            'unique_count': len(set(urls))
        }
        CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(CACHE_FILE, 'w') as f:
            json.dump(cache, f, indent=2)
        logger.info(f"Saved cache with {cache['count']} URLs")
        return True
    except IOError as e:
        logger.error(f"Failed to save cache: {e}")
        return False


def load_checkpoint() -> Optional[Dict[str, Any]]:
    """Load checkpoint if exists.
    
    Returns:
        Checkpoint data dict or None if no checkpoint exists
    """
    if not CHECKPOINT_FILE.exists():
        return None
    
    try:
        with open(CHECKPOINT_FILE, 'r') as f:
            checkpoint = json.load(f)
        logger.info(f"Found checkpoint from {checkpoint['timestamp']} (processed {checkpoint['batches_completed']}/{checkpoint['total_batches']} batches)")
        return checkpoint
    except (json.JSONDecodeError, KeyError) as e:
        logger.error(f"Failed to load checkpoint: {e}")
        return None


def save_checkpoint(batch_index: int, total_batches: int, urls_so_far: List[str], batch_name: str) -> bool:
    """Save progress checkpoint.
    
    Args:
        batch_index: Current batch index
        total_batches: Total number of batches
        urls_so_far: URLs collected so far
        batch_name: Name of the batch just completed
        
    Returns:
        True if save successful, False otherwise
    """
    try:
        checkpoint = {
            'timestamp': datetime.now().isoformat(),
            'batches_completed': batch_index + 1,
            'total_batches': total_batches,
            'last_batch': batch_name,
            'urls_so_far': urls_so_far,
            'urls_count': len(urls_so_far)
        }
        CHECKPOINT_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(CHECKPOINT_FILE, 'w') as f:
            json.dump(checkpoint, f, indent=2)
        return True
    except IOError as e:
        logger.error(f"Failed to save checkpoint: {e}")
        return False


def clear_checkpoint() -> None:
    """Remove checkpoint file."""
    if CHECKPOINT_FILE.exists():
        CHECKPOINT_FILE.unlink()
        logger.info("Cleared checkpoint file")


def write_urls_to_file(ul: List[str]) -> bool:
    """Writes a list of company urls to a file.
    
    Args:
        ul: List of company URLs
        
    Returns:
        True if write successful, False otherwise
    """
    try:
        START_URLS_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(START_URLS_FILE, 'w') as f:
            json.dump(ul, f, indent=2)
        logger.info(f"Wrote {len(ul)} URLs to start_urls.txt")
        return True
    except IOError as e:
        logger.error(f"Failed to write URLs to file: {e}")
        return False


def yc_links_extractor(force_refresh: bool = False, filter_batches: Optional[List[str]] = None, recent: Optional[int] = None, resume: bool = True) -> bool:
    """Run the main script to write all start urls to a file.
    
    Args:
        force_refresh: If True, ignore cache and re-scrape
        filter_batches: List of specific batch names to scrape (e.g., ['W24', 'S24'])
        recent: Number of most recent batches to scrape
        resume: If True, resume from checkpoint if available
    
    Returns:
        True if scraping completed successfully, False otherwise
    """
    # Check cache first (unless force refresh or filtering)
    if not force_refresh and not filter_batches and not recent:
        cache = load_cache()
        if cache:
            logger.info("Using cached URLs. Use --force-refresh to re-scrape.")
            success = write_urls_to_file(cache['urls'])
            if success:
                logger.info(f"✅ Loaded {cache['count']} URLs from cache (age: {cache.get('timestamp', 'unknown')})")
            return success
    
    logger.info(f"Attempting to scrape links from {page}")
    
    # Check for checkpoint
    checkpoint = load_checkpoint() if resume else None
    start_index = 0
    ulist = []
    
    if checkpoint:
        logger.info(f"Resuming from checkpoint: {checkpoint['batches_completed']}/{checkpoint['total_batches']} batches completed")
        ulist = checkpoint['urls_so_far']
        start_index = checkpoint['batches_completed']
    
    # Create driver
    driver = make_driver()
    if driver is None:
        logger.error("Failed to create WebDriver. Exiting.")
        return False
    
    try:
        if not get_page_source(driver):
            return False
        
        if not click_see_all_options(driver):
            return False
        
        # compile an array of batches (checkbox elements)
        batches = list(compile_batches(driver, filter_batches=filter_batches, recent=recent))
        if not batches:
            logger.error("No batches found. Scraping cannot continue.")
            return False
        
        total_batches = len(batches)
        logger.info(f"Total batches to process: {total_batches}")
        
        # Skip already processed batches if resuming
        if start_index > 0:
            logger.info(f"Skipping first {start_index} batches (already processed)")
            batches = batches[start_index:]
        
        for idx, b in enumerate(tqdm(batches, desc="Processing batches", initial=start_index, total=total_batches)):
            actual_idx = start_index + idx
            batch_name = b.text
            
            # filter companies
            b.click()

            # scroll down to load all companies
            scroll_to_bottom(driver)

            # fetch links and append them to ulist
            urls = [u for u in fetch_url_paths(driver)]
            logger.debug(f"Found {len(urls)} company links for batch: {batch_name}")
            ulist.extend(urls)

            # uncheck the batch checkbox
            b.click()
            
            # Save checkpoint after each batch
            save_checkpoint(actual_idx, total_batches, ulist, batch_name)
        
        unique_count = len(set(ulist))
        logger.info(f"Total unique company links collected: {unique_count}")
        
        # Write to file
        success = write_urls_to_file(ulist)
        
        if success:
            # Save to cache (only if no filtering was applied)
            if not filter_batches and not recent:
                save_cache(ulist)
            # Clear checkpoint on successful completion
            clear_checkpoint()
            logger.info("✅ Scraping completed successfully!")
        
        return success
        
    except KeyboardInterrupt:
        logger.warning("Scraping interrupted by user. Progress saved in checkpoint.")
        logger.info("Run the script again to resume from where you left off.")
        return False
    except Exception as e:
        logger.error(f"Error during scraping: {e}")
        logger.info("Progress saved in checkpoint. Run the script again to resume.")
        return False
    finally:
        driver.quit()
        logger.info("WebDriver closed")


def main():
    """Parse command-line arguments and run the scraper."""
    parser = argparse.ArgumentParser(
        description='Y Combinator Company URL Scraper',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s                          # Use cache if available, otherwise scrape all
  %(prog)s --force-refresh          # Ignore cache and re-scrape everything
  %(prog)s --recent 3               # Scrape only 3 most recent batches
  %(prog)s --batches W24 S24        # Scrape specific batches only
  %(prog)s --no-resume              # Start fresh, ignore checkpoint
  %(prog)s --recent 5 --force-refresh  # Scrape 5 recent batches, skip cache
        """
    )
    
    parser.add_argument(
        '--force-refresh',
        action='store_true',
        help='Ignore cache and re-scrape all URLs'
    )
    
    parser.add_argument(
        '--recent',
        type=int,
        metavar='N',
        help='Scrape only N most recent batches'
    )
    
    parser.add_argument(
        '--batches',
        nargs='+',
        metavar='BATCH',
        help='Scrape specific batches (e.g., W24 S24 or "Winter 2024")'
    )
    
    parser.add_argument(
        '--no-resume',
        action='store_true',
        help='Start fresh, ignore existing checkpoint'
    )
    
    parser.add_argument(
        '--clear-cache',
        action='store_true',
        help='Clear cache and checkpoint files, then exit'
    )
    
    args = parser.parse_args()
    
    # Handle --clear-cache
    if args.clear_cache:
        if CACHE_FILE.exists():
            CACHE_FILE.unlink()
            logger.info(f"Removed cache file: {CACHE_FILE}")
        if CHECKPOINT_FILE.exists():
            CHECKPOINT_FILE.unlink()
            logger.info(f"Removed checkpoint file: {CHECKPOINT_FILE}")
        logger.info("Cache and checkpoint cleared.")
        return
    
    # Run scraper with options
    success = yc_links_extractor(
        force_refresh=args.force_refresh,
        filter_batches=args.batches,
        recent=args.recent,
        resume=not args.no_resume
    )
    
    if not success:
        exit(1)


if __name__ == '__main__':
    main()
