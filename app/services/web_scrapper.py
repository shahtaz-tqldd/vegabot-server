from app.utils.logger import logger
from dataclasses import dataclass
import asyncio, re, random

from playwright.async_api import async_playwright

@dataclass
class Data:
    title: str = None
    text: str = None


@dataclass
class SuccessContent:
    data: Data
    success: bool = True
    

@dataclass
class FailedContent:
    success: bool = False
    error: str = None


class WebScraper:

    def __init__(self):
        self.user_agents = [
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0',
            'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.1 Safari/605.1.15',
            'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        ]

    async def scrape_text_content(self, url, wait_time=None, custom_selector=None):
        """
        Scrape text content from a webpage using Playwright with anti-detection measures
        
        Args:
            url (str): The URL to scrape
            wait_time (int): Optional custom wait time in seconds
            custom_selector (str): Optional CSS selector to focus on specific content (e.g., 'main', 'article')
            
        Returns:
            dict: Contains 'text', 'title', and 'status'
        """
        async with async_playwright() as p:
            try:
                # Open a hidden browser (no visible window pop-up) and apply safety adjustments
                # so the target website thinks it is dealing with a normal user browser.
                browser = await p.chromium.launch(
                    headless=True,
                    args=[
                        '--no-sandbox',
                        '--disable-blink-features=AutomationControlled', # Disables internal indicators that shout "I am a robot!"
                        '--disable-dev-shm-usage',
                        '--disable-extensions',
                        '--no-first-run',
                        '--no-default-browser-check',
                        '--disable-default-apps',
                        '--disable-features=VizDisplayCompositor'
                    ]
                )
                
                # Set up a fake profile using one of our browser descriptions, a random screen resolution, and fake time settings.
                context = await browser.new_context(
                    user_agent=random.choice(self.user_agents),
                    viewport={'width': random.randint(1024, 1920), 'height': random.randint(768, 1080)},
                    locale='en-US',
                    timezone_id='America/New_York'
                )
                
                # Forcefully inject rules into the browser environment before the webpage loads.
                # This covers up automated settings and replaces them with standard settings a human would have.
                await context.add_init_script("""
                    // Change the "webdriver" setting to read False so websites think a human is browsing.
                    Object.defineProperty(navigator, 'webdriver', {
                        get: () => false,
                    });
                    
                    // Add fake browser plugins to make the profile look older and realistic.
                    Object.defineProperty(navigator, 'plugins', {
                        get: () => [1, 2, 3, 4, 5],
                    });
                    
                    // Tell the site we use standard English languages.
                    Object.defineProperty(navigator, 'languages', {
                        get: () => ['en-US', 'en'],
                    });
                    
                    // Make the script believe it's running inside a real Google Chrome browser window.
                    window.chrome = {
                        runtime: {},
                    };
                    
                    // Automatically block standard notification popups so they don't block the screen.
                    const originalQuery = window.navigator.permissions.query;
                    window.navigator.permissions.query = (parameters) => (
                        parameters.name === 'notifications' ?
                        Promise.resolve({ state: 'denied' }) :
                        originalQuery(parameters)
                    );
                """)
                
                # Open up a brand new blank tab inside our hidden browser window
                page = await context.new_page()
                
                # Attach extra data tags to requests, making them look identical to everyday web traffic
                await page.set_extra_http_headers({
                    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
                    'Accept-Language': 'en-US,en;q=0.5',
                    'Accept-Encoding': 'gzip, deflate, br',
                    'DNT': '1',
                    'Connection': 'keep-alive',
                    'Upgrade-Insecure-Requests': '1'
                })
                
                # Rest for a random amount of time (1 to 3 seconds) before requesting the link. Humans don't click instantly!
                await asyncio.sleep(random.uniform(1, 3))
                
                logger.info(f"Navigating to: {url}")
                
                # Tell the browser tab to visit the requested link and wait up to 120 seconds for the skeleton structure to load
                response = await page.goto(
                    url, 
                    wait_until='domcontentloaded',
                    timeout=30000 * 4 # 120 seconds
                )

                if not response:
                    logger.warning(
                        f"{response.status if response else 'No response'} - continuing anyway"
                    )

                # Dwell on the page for a few seconds to simulate a person reading the screen.
                wait_duration = wait_time if wait_time else random.uniform(2, 5)
                await asyncio.sleep(wait_duration)
                
                # Wait for ongoing background network noise (like tracking scripts or image downloads) to calm down.
                try:
                    await page.wait_for_load_state('networkidle', timeout=10000)
                except:
                    logger.warning("Network idle timeout, proceeding anyway")
                
                # Look for and click common "Accept Cookies" or pop-up exit options to clear the screen area.
                await self._handle_popups(page)
                
                # Scroll down the layout step-by-step so images or extra text sections are forced to appear.
                await self._scroll_page(page)
                
                # Grab the readable text content currently visible on the page
                if custom_selector:
                    # Look inside a specific box/area on the page if requested (like just the main article box)
                    dom_text = await page.evaluate(f"""
                        () => {{
                            const element = document.querySelector('{custom_selector}');
                            return element ? element.innerText : document.body.innerText;
                        }}
                    """)
                else:
                    # Otherwise, grab every single piece of readable text from top to bottom
                    dom_text = await page.evaluate("() => document.body.innerText")

                # Grab the official page title tab label
                title = await page.title()
                
                # Shut down the hidden browser completely to clear out device memory
                await browser.close()
            
                # Clean up ugly extra spacing and common footer warning terms
                cleaned_text = self._clean_text(dom_text)

                # Return the success object with the parsed clean data
                return SuccessContent(
                    success=True, 
                    data=Data(
                        title=title,
                        text=cleaned_text
                    )
                )

            except Exception as e:
                logger.error(f"Error scraping {url}: {str(e)}", exc_info=True)

                try:
                    await browser.close()
                except:
                    pass

                return FailedContent(
                    success=False,
                    error=str(e)
                )

    async def _handle_popups(self, page):
        """Handle common pop-ups and overlays"""
        popup_selectors = [
            '[class*="cookie"] button',
            '[class*="consent"] button',
            '[class*="popup"] [class*="close"]',
            '[class*="modal"] [class*="close"]',
            '[class*="overlay"] [class*="close"]',
            'button[class*="accept"]',
            'button[class*="agree"]',
            '.popup-close',
            '.modal-close',
            '[aria-label*="close"]',
            '[aria-label*="dismiss"]'
        ]
        
        for selector in popup_selectors:
            try:
                element = await page.query_selector(selector)
                if element and await element.is_visible():
                    await element.click()
                    await asyncio.sleep(0.5)
                    break
            except:
                continue

    async def _scroll_page(self, page):
        """Scroll through the page to load lazy content"""
        try:
            # Check the full vertical length of the webpage canvas area
            page_height = await page.evaluate("document.body.scrollHeight")
            
            # Start a scrolling loop that moves down by 500 pixels at a time
            scroll_position = 0
            chunk_size = 500
            
            while scroll_position < page_height:
                await page.evaluate(f"window.scrollTo(0, {scroll_position})")
                await asyncio.sleep(random.uniform(0.5, 1)) # Wait briefly between scrolls to feel like a real human scroll wheel
                scroll_position += chunk_size
                
                # Update total page height check in case scrolling caused more hidden text to load underneath
                new_height = await page.evaluate("document.body.scrollHeight")
                if new_height > page_height:
                    page_height = new_height
            
            # Reset view window focus back to the very top coordinates of the layout
            await page.evaluate("window.scrollTo(0, 0)")
            await asyncio.sleep(1)
            
        except Exception as e:
            logger.warning(f"Error during scrolling: {e}")

    def _clean_text(self, text):
        """
        Simple text cleanup - no HTML parsing needed since innerText already gives clean text
        
        Args:
            text (str): Raw text from innerText
            
        Returns:
            str: Cleaned text
        """
        if not text:
            return ""
        
        try:
            # Flatten multi-line spacing, heavy indents, and massive blank rows down to standard single spaces
            text = re.sub(r'\s+', ' ', text)
            
            # Specific recurring legal text sequences we want to throw away out of the output string data
            boilerplate_patterns = [
                r'Cookie\s+Policy',
                r'Privacy\s+Policy', 
                r'Terms\s+(and\s+Conditions|of\s+Service)',
            ]
            
            # Search out and remove those target patterns if they exist anywhere in our data dump
            for pattern in boilerplate_patterns:
                text = re.sub(pattern, '', text, flags=re.IGNORECASE)
            
            # Trim off any hanging spaces left on the absolute start or end sides of the block
            text = text.strip()
            
            return text
            
        except Exception as e:
            logger.error(f"Error cleaning text: {e}")
            return text
