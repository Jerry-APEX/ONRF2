"""Local UI smoke test. Start localhost with test credentials, then run this file.

Requires Playwright and Chromium. Set ONRF2_TEST_ADMIN_PASSWORD in the process
environment. Only a loopback HTTP origin is accepted: this test resets test data.
Physical iPhone/Android camera acceptance is a separate, manual step.
"""
import os
from urllib.parse import urlparse

from playwright.sync_api import expect, sync_playwright

base = os.environ.get('ONRF2_LOCAL_TEST_URL', 'http://127.0.0.1:8765').rstrip('/')
if urlparse(base).hostname not in {'127.0.0.1', 'localhost', '::1'}:
    raise RuntimeError('This resetting smoke test only supports localhost.')
password = os.environ['ONRF2_TEST_ADMIN_PASSWORD']

with sync_playwright() as playwright:
    browser = playwright.chromium.launch(headless=True)
    page = browser.new_page(viewport={'width':390,'height':844})
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.goto(base)
    page.locator('#username').fill('admin')
    page.locator('#password').fill(password)
    page.locator('#loginButton').click()
    expect(page.locator('#portal')).to_be_visible()
    csrf = page.request.get(base + '/api/me').json()['csrf']
    reset = page.request.post(base + '/api/reset', data={'confirmation':'RESET TEST DATA'}, headers={'X-CSRF':csrf})
    assert reset.status == 200
    outcomes = ['ACCESS GRANTED'] * 4 + ['ALREADY SCANNED','ACCESS DENIED','ACCESS DENIED','ALREADY SCANNED']
    for number, outcome in enumerate(outcomes):
        page.locator('[data-tab="samples"]').click()
        expect(page.locator('.sample-card')).to_have_count(8)
        page.locator('.simulate-button').nth(number).click()
        expect(page.locator('#resultTitle')).to_have_text(outcome)
        if number == 5:
            expect(page.locator('#resultMessage')).to_have_text('This ticket has been cancelled.')
        if number == 6:
            expect(page.locator('#resultMessage')).to_have_text('Invalid ticket. Please contact event staff.')
    for width in [320,375,390,430,768,1440]:
        page.set_viewport_size({'width':width,'height':900})
        for section in ['scan','samples','admin']:
            page.locator(f'[data-tab="{section}"]').click()
            if section == 'admin':
                expect(page.locator('#statTotal')).to_have_text('6')
                expect(page.locator('#statRejected')).to_have_text('4')
            assert page.evaluate('() => document.body.scrollWidth <= innerWidth')
    page.locator('#resetData').click()
    expect(page.locator('#resetDialog')).to_be_visible()
    page.locator('#resetDialog button[value="cancel"]').click()
    expect(page.locator('#statRejected')).to_have_text('4')
    page.locator('#resetData').click()
    page.locator('#confirmReset').click()
    expect(page.locator('#statRejected')).to_have_text('0')
    assert not errors, errors
    page.locator('#adminSignout').click()
    expect(page.locator('#loginPage')).to_be_visible()
    expect(page.locator('#portal')).to_be_hidden()
    print('Browser smoke passed: all eight samples, six viewport widths, reset confirmation, statistics, sign out; no JavaScript errors.')
    browser.close()
