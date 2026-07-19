import json
import logging
from pathlib import Path
from typing import List, Dict, Any, Optional, Generator
import scrapy


logger = logging.getLogger(__name__)


def make_start_urls_list() -> List[str]:
    """Returns a list with the start urls.
    
    Returns:
        List[str]: List of company URLs to scrape
        
    Raises:
        FileNotFoundError: If start_urls.txt doesn't exist
        ValueError: If the file contains invalid JSON
    """
    start_urls_path = Path(__file__).parent.parent / 'start_urls.txt'
    
    if not start_urls_path.exists():
        raise FileNotFoundError(
            f"Start URLs file not found at {start_urls_path}. "
            "Please run yc_links_extractor.py first."
        )
    
    try:
        with open(start_urls_path, 'r') as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON in start URLs file: {e}")


class YCombinator(scrapy.Spider):
    """Crawls ycombinator.com/companies and extracts data about each company."""
    name = 'YCombinatorScraper'
    start_urls = make_start_urls_list()

    def parse(self, response) -> Generator[Dict[str, Any], None, None]:
        """Parse company page and extract company data.
        
        Args:
            response: Scrapy response object
            
        Yields:
            Dict containing company information
        """
        # Get the JSON object from data-page attribute
        st = response.css('[data-page]::attr(data-page)').get()
        
        if st is None:
            self.logger.warning(f"No data-page attribute found at {response.url}")
            return
        
        try:
            # Parse JSON data
            jo = json.loads(st)
            jc = jo['props']['company']
            
            founders = jc.get('founders', [])
            
            # Extract and yield company data with enhanced founder information
            yield {
                'company_id': jc.get('id'),
                'company_name': jc.get('name'),
                'short_description': jc.get('one_liner'),
                'long_description': jc.get('long_description'),
                'batch': jc.get('batch_name'),
                'status': jc.get('ycdc_status'),
                'tags': jc.get('tags', []),
                'location': jc.get('location'),
                'country': jc.get('country'),
                'year_founded': jc.get('year_founded'),
                'num_founders': len(founders),
                'founders_names': [f.get('full_name') for f in founders if f.get('full_name')],
                'founder_details': [
                    {
                        'name': f.get('full_name'),
                        'title': f.get('title'),
                        'bio': f.get('founder_bio'),
                        'linkedin_url': f.get('linkedin_url'),
                        'twitter_url': f.get('twitter_url'),
                    }
                    for f in founders
                ],
                'team_size': jc.get('team_size'),
                'website': jc.get('website'),
                'cb_url': jc.get('cb_url'),
                'linkedin_url': jc.get('linkedin_url'),
            }
        except (json.JSONDecodeError, KeyError, TypeError) as e:
            self.logger.error(f"Failed to parse company data from {response.url}: {e}")
            return
