"""
Gotmon Server Checker - Selenium Automation
Checks ServerSitter status and CPU usage via PerfStat
"""

import re
import time
import logging
from typing import Dict, Optional, Tuple
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.common.exceptions import TimeoutException, NoSuchElementException
from webdriver_manager.chrome import ChromeDriverManager

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class GotmonChecker:
    """Automates Gotmon GUI to check server health status."""

    def __init__(self, config: dict):
        self.config = config
        self.gotmon_url = config.get('gotmon', {}).get('url', 'http://gotmon.int.thomsonreuters.com/index.php')
        self.timeout = config.get('browser', {}).get('timeout', 30)
        self.headless = config.get('browser', {}).get('headless', False)
        self.cpu_threshold = config.get('thresholds', {}).get('cpu_max_percentage', 80)
        self.driver = None

    def _init_driver(self):
        """Initialize Chrome WebDriver."""
        chrome_options = Options()
        if self.headless:
            chrome_options.add_argument('--headless')
        chrome_options.add_argument('--no-sandbox')
        chrome_options.add_argument('--disable-dev-shm-usage')
        chrome_options.add_argument('--disable-gpu')
        chrome_options.add_argument('--window-size=1920,1080')

        service = Service(ChromeDriverManager().install())
        self.driver = webdriver.Chrome(service=service, options=chrome_options)
        self.driver.implicitly_wait(10)

    def _close_driver(self):
        """Close the WebDriver."""
        if self.driver:
            self.driver.quit()
            self.driver = None

    def check_server(self, server_name: str) -> Dict:
        """
        Main method to check server health.

        Returns a dict with:
        - server_sitter_status: 'green' or 'red'
        - perfstat_accessible: True/False
        - perfstat_error: Error message if PerfStat failed
        - cpu_usage: CPU percentage (max value)
        - action: 'close' or 'escalate'
        - escalate_to: Snow group if escalation needed
        - escalation_description: Description for escalation
        - close_statement: Statement if INC should be closed
        """
        result = {
            'server_name': server_name,
            'server_sitter_status': None,
            'perfstat_accessible': False,
            'perfstat_error': None,
            'cpu_usage': None,
            'action': None,
            'escalate_to': None,
            'escalation_description': None,
            'close_statement': None,
            'error': None
        }

        try:
            self._init_driver()

            # Step 1: Open Gotmon and search for server
            logger.info(f"Opening Gotmon for server: {server_name}")
            server_sitter_status, perfstat_url = self._check_gotmon(server_name)
            result['server_sitter_status'] = server_sitter_status

            # Step 2: Check PerfStat if URL available
            if perfstat_url:
                try:
                    cpu_usage = self._check_perfstat(perfstat_url)
                    result['perfstat_accessible'] = True
                    result['cpu_usage'] = cpu_usage
                except Exception as e:
                    logger.error(f"PerfStat error: {e}")
                    result['perfstat_accessible'] = False
                    result['perfstat_error'] = str(e)
            else:
                result['perfstat_accessible'] = False
                result['perfstat_error'] = "PerfStat link not found"

            # Step 3: Determine action based on conditions
            result = self._determine_action(result)

        except Exception as e:
            logger.error(f"Error checking server: {e}")
            result['error'] = str(e)

        finally:
            self._close_driver()

        return result

    def _check_gotmon(self, server_name: str) -> Tuple[str, Optional[str]]:
        """
        Check Gotmon for server status.

        Returns:
        - server_sitter_status: 'green' or 'red'
        - perfstat_url: URL to PerfStat page (or None)
        """
        self.driver.get(self.gotmon_url)
        wait = WebDriverWait(self.driver, self.timeout)

        # Wait for page to load
        wait.until(EC.presence_of_element_located((By.NAME, "ci")))

        # Enter server name in ServiceNow CI textarea
        ci_textarea = self.driver.find_element(By.NAME, "ci")
        ci_textarea.clear()
        ci_textarea.send_keys(server_name)

        # Select required checkboxes
        self._select_checkbox("ServerSitter")
        self._select_checkbox("ServerSitter Status (very slow)")

        # Click Submit button
        submit_btn = self.driver.find_element(By.XPATH, "//input[@value='Submit']")
        submit_btn.click()

        # Wait for results table to load
        time.sleep(3)  # Give time for the table to populate
        wait.until(EC.presence_of_element_located((By.XPATH, "//table")))

        # Find ServerSitter Status column for our server
        server_sitter_status = self._get_server_sitter_status(server_name)

        # Find PerfStat link
        perfstat_url = self._get_perfstat_url(server_name)

        return server_sitter_status, perfstat_url

    def _select_checkbox(self, label_text: str):
        """Select a checkbox by its label text."""
        try:
            # Find checkbox by looking for label text
            checkbox = self.driver.find_element(
                By.XPATH,
                f"//input[@type='checkbox' and following-sibling::text()[contains(., '{label_text}')] or "
                f"@type='checkbox' and ../text()[contains(., '{label_text}')]]"
            )
            if not checkbox.is_selected():
                checkbox.click()
        except NoSuchElementException:
            # Try alternative approach - find by value or id containing the text
            try:
                checkboxes = self.driver.find_elements(By.XPATH, "//input[@type='checkbox']")
                for cb in checkboxes:
                    parent_text = cb.find_element(By.XPATH, "..").text
                    if label_text.lower() in parent_text.lower():
                        if not cb.is_selected():
                            cb.click()
                        return
            except Exception as e:
                logger.warning(f"Could not select checkbox '{label_text}': {e}")

    def _get_server_sitter_status(self, server_name: str) -> str:
        """
        Get ServerSitter status (green/red) from the results table.
        The status is shown as colored text in the ServerSitter Status column.
        """
        try:
            # Look for the ServerSitter Status cell for this server
            # The status text appears with color styling (green or red)

            # First, try to find by looking at the last column which contains server links
            status_cells = self.driver.find_elements(
                By.XPATH,
                "//td[contains(@class, 'ServerSitter') or contains(., 'TracePingNmapSSLogs')]"
            )

            for cell in status_cells:
                cell_html = cell.get_attribute('innerHTML')
                cell_text = cell.text.lower()

                # Check if this cell contains our server name
                if server_name.lower() in cell_text or server_name.lower() in cell_html.lower():
                    # Check the color of the text
                    # Green status: style contains 'green' or class indicates green
                    # Red status: style contains 'red' or class indicates red

                    # Look for colored links/spans
                    green_elements = cell.find_elements(By.XPATH, ".//*[contains(@style, 'green') or contains(@color, 'green')]")
                    red_elements = cell.find_elements(By.XPATH, ".//*[contains(@style, 'red') or contains(@color, 'red')]")

                    if green_elements:
                        return 'green'
                    elif red_elements:
                        return 'red'

            # Alternative: Look for the font color in the HTML
            page_source = self.driver.page_source

            # Find the section containing server name and check surrounding color
            server_pattern = rf'{server_name}.*?</td>'
            matches = re.findall(server_pattern, page_source, re.IGNORECASE | re.DOTALL)

            for match in matches:
                if 'color' in match.lower():
                    if 'green' in match.lower():
                        return 'green'
                    elif 'red' in match.lower():
                        return 'red'

            # If we found the server but couldn't determine color, check the entire row
            # Look in the rightmost column (ServerSitter Status)
            rows = self.driver.find_elements(By.XPATH, "//tr")
            for row in rows:
                row_text = row.text.lower()
                if server_name.lower() in row_text:
                    row_html = row.get_attribute('innerHTML')
                    # Count green vs red occurrences in the row
                    green_count = row_html.lower().count('green')
                    red_count = row_html.lower().count('red')
                    if green_count > red_count:
                        return 'green'
                    elif red_count > 0:
                        return 'red'

            return 'unknown'

        except Exception as e:
            logger.error(f"Error getting ServerSitter status: {e}")
            return 'unknown'

    def _get_perfstat_url(self, server_name: str) -> Optional[str]:
        """Find the PerfStat link for the given server."""
        try:
            # PerfStat link appears as "perfstat" in the Operational Status column
            # It's a link that opens the Windows PerfStat page

            # Try to find perfstat link in the row containing our server
            perfstat_links = self.driver.find_elements(
                By.XPATH,
                f"//a[contains(translate(text(), 'PERFSTAT', 'perfstat'), 'perfstat')]"
            )

            if perfstat_links:
                # Return the first matching perfstat link
                return perfstat_links[0].get_attribute('href')

            # Alternative: Look for any link containing the server name and 'assets' (PerfStat domain)
            asset_links = self.driver.find_elements(
                By.XPATH,
                f"//a[contains(@href, 'assets') and contains(@href, '{server_name}')]"
            )

            if asset_links:
                return asset_links[0].get_attribute('href')

            # Try clicking on the server row to see if perfstat becomes visible
            # Sometimes it's revealed on hover or click

            return None

        except Exception as e:
            logger.error(f"Error finding PerfStat URL: {e}")
            return None

    def _check_perfstat(self, perfstat_url: str) -> float:
        """
        Open PerfStat and get CPU usage.

        Returns the maximum CPU usage percentage.
        """
        # Open PerfStat in new tab
        self.driver.execute_script(f"window.open('{perfstat_url}', '_blank');")
        time.sleep(2)

        # Switch to new tab
        self.driver.switch_to.window(self.driver.window_handles[-1])

        wait = WebDriverWait(self.driver, self.timeout)

        # Wait for PerfStat page to load
        wait.until(EC.presence_of_element_located((By.XPATH, "//input[@value='CPU' or @id='CPU']")))

        # Select CPU radio button (usually already selected)
        try:
            cpu_radio = self.driver.find_element(By.XPATH, "//input[@value='CPU' or contains(@id, 'cpu')]")
            if not cpu_radio.is_selected():
                cpu_radio.click()
        except:
            pass  # CPU might already be selected

        # Click "Create Graphs" button
        create_graphs_btn = self.driver.find_element(
            By.XPATH,
            "//input[@value='Create Graphs' or contains(@value, 'Graph')]"
        )
        create_graphs_btn.click()

        # Wait for graphs to load
        time.sleep(5)

        # Parse CPU usage from the page
        # The CPU %usage graph shows: "Min:X% Ave:Y% Max:Z%"
        page_source = self.driver.page_source

        # Look for Max:XX% pattern in CPU %usage section
        max_pattern = r'Max:(\d+(?:\.\d+)?)%'
        matches = re.findall(max_pattern, page_source)

        if matches:
            # Return the first (or highest) max value found
            max_values = [float(m) for m in matches]
            return max(max_values)

        # Alternative: Look for Ave (average) if Max not found
        ave_pattern = r'Ave:(\d+(?:\.\d+)?)%'
        ave_matches = re.findall(ave_pattern, page_source)

        if ave_matches:
            ave_values = [float(m) for m in ave_matches]
            return max(ave_values)

        raise Exception("Could not parse CPU usage from PerfStat graphs")

    def _determine_action(self, result: Dict) -> Dict:
        """
        Determine whether to close or escalate based on the checks.

        Decision matrix:
        - Green + PerfStat OK + CPU < 80% -> Close
        - Red + PerfStat OK + CPU > 80% -> Escalate (red_status_high_cpu)
        - Green + PerfStat Error -> Escalate (perfstat_error)
        """
        ss_status = result.get('server_sitter_status', '').lower()
        perfstat_ok = result.get('perfstat_accessible', False)
        cpu_usage = result.get('cpu_usage')

        # Case 1: Everything is healthy - CLOSE
        if ss_status == 'green' and perfstat_ok and cpu_usage is not None and cpu_usage < self.cpu_threshold:
            result['action'] = 'close'
            result['close_statement'] = self.config.get('close_statement',
                f"Server health check passed. ServerSitter: GREEN, CPU: {cpu_usage}%")

        # Case 2: Red status with high CPU - ESCALATE to red_status_high_cpu team
        elif ss_status == 'red' and cpu_usage is not None and cpu_usage >= self.cpu_threshold:
            escalation = self.config.get('escalation', {}).get('red_status_high_cpu', {})
            result['action'] = 'escalate'
            result['escalate_to'] = escalation.get('snow_group', 'ESCALATION_GROUP')
            result['escalation_description'] = escalation.get('description',
                f"ServerSitter RED, CPU at {cpu_usage}%")

        # Case 3: Green status but PerfStat error - ESCALATE to perfstat_error team
        elif ss_status == 'green' and not perfstat_ok:
            escalation = self.config.get('escalation', {}).get('perfstat_error', {})
            result['action'] = 'escalate'
            result['escalate_to'] = escalation.get('snow_group', 'PERFSTAT_SUPPORT')
            result['escalation_description'] = escalation.get('description',
                f"ServerSitter GREEN but PerfStat unavailable: {result.get('perfstat_error', 'Unknown error')}")

        # Case 4: Red status but CPU is OK - still escalate (server has issues)
        elif ss_status == 'red':
            escalation = self.config.get('escalation', {}).get('red_status_high_cpu', {})
            result['action'] = 'escalate'
            result['escalate_to'] = escalation.get('snow_group', 'ESCALATION_GROUP')
            cpu_info = f", CPU: {cpu_usage}%" if cpu_usage else ""
            result['escalation_description'] = f"ServerSitter status is RED{cpu_info}. Investigation required."

        # Case 5: Unknown status - escalate for investigation
        else:
            result['action'] = 'escalate'
            result['escalate_to'] = self.config.get('escalation', {}).get('perfstat_error', {}).get('snow_group', 'SUPPORT')
            result['escalation_description'] = f"Unable to determine server status. Manual check required."

        return result


# For testing
if __name__ == "__main__":
    import yaml

    with open('config.yaml', 'r') as f:
        config = yaml.safe_load(f)

    checker = GotmonChecker(config)
    result = checker.check_server("c835uvdctxdr")
    print(result)
