"""Local UI smoke test. Start localhost with test credentials, then run this file.

Requires Playwright and Chromium. Set ONRF2_TEST_ADMIN_PASSWORD in the process
environment. Only a loopback HTTP origin is accepted: this test resets test data.
Physical iPhone/Android camera acceptance is a separate, manual step.
"""
import os
import ast
import json
import time
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import expect, sync_playwright

base = os.environ.get('ONRF2_LOCAL_TEST_URL', 'http://127.0.0.1:8765').rstrip('/')
if urlparse(base).hostname not in {'127.0.0.1', 'localhost', '::1'}:
    raise RuntimeError('This resetting smoke test only supports localhost.')
password = os.environ['ONRF2_TEST_ADMIN_PASSWORD']
root = Path(__file__).resolve().parents[1]
new_samples = json.loads((root / 'test_samples_20.json').read_text())['samples']
original = next(ast.literal_eval(node.value) for node in ast.parse((root / 'app.py').read_text()).body
                if isinstance(node, ast.Assign) and any(isinstance(name, ast.Name) and name.id == 'SAMPLES' for name in node.targets))

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
    page.locator('[data-tab="samples"]').click()
    expect(page.locator('#sampleCollection')).to_have_value('alphanumeric-20')
    for collection, tokens, outcomes in [
        ('alphanumeric-20', [sample['token'] for sample in new_samples], [sample['sequential_result'] for sample in new_samples]),
        ('original', [sample[1] for sample in original], ['ACCESS GRANTED'] * 4 + ['ALREADY SCANNED','ACCESS DENIED','ACCESS DENIED','ALREADY SCANNED']),
    ]:
        page.locator('[data-tab="samples"]').click()
        page.locator('#sampleCollection').select_option(collection)
        expect(page.locator('.sample-card')).to_have_count(len(tokens))
        decoded = page.locator('.sample-qr').evaluate_all('''async images => Promise.all(images.map(async image => {
            await image.decode();
            const canvas = document.createElement('canvas');
            canvas.width = image.naturalWidth; canvas.height = image.naturalHeight;
            const context = canvas.getContext('2d'); context.drawImage(image, 0, 0);
            const frame = context.getImageData(0, 0, canvas.width, canvas.height);
            return window.jsQR(frame.data, frame.width, frame.height)?.data || null;
        }))''')
        assert decoded == tokens
        for number, outcome in enumerate(outcomes):
            page.locator('[data-tab="samples"]').click()
            expect(page.locator('.sample-card')).to_have_count(len(tokens))
            page.locator('.simulate-button').nth(number).click()
            expect(page.locator('#resultTitle')).to_have_text(outcome)
            if number == 5:
                expect(page.locator('#resultMessage')).to_have_text('This ticket has been cancelled.')
            if number == 6:
                expect(page.locator('#resultMessage')).to_have_text('Invalid ticket. Please contact event staff.')
    page.locator('[data-tab="samples"]').click()
    def delayed_collection(route):
        response = route.fetch()
        time.sleep(0.3)
        route.fulfill(response=response)
    page.route(base + '/api/collections/alphanumeric-20/samples', delayed_collection)
    page.evaluate('''() => {
        const collection = document.getElementById('sampleCollection');
        collection.value = 'alphanumeric-20'; collection.dispatchEvent(new Event('change'));
        setTimeout(() => { collection.value = 'original'; collection.dispatchEvent(new Event('change')); }, 25);
    }''')
    expect(page.locator('#sampleCollection')).to_have_value('original')
    expect(page.locator('.sample-card')).to_have_count(len(original))
    page.wait_for_timeout(400)
    expect(page.locator('.sample-card')).to_have_count(len(original))
    expect(page.locator('#sampleCounts')).to_have_text('8 SAMPLES · 6 TEST TICKETS')
    page.unroute(base + '/api/collections/alphanumeric-20/samples', delayed_collection)
    for width in [320,375,390,430,768,1440]:
        page.set_viewport_size({'width':width,'height':900})
        for section in ['scan','samples','admin']:
            page.locator(f'[data-tab="{section}"]').click()
            if section == 'admin':
                expect(page.locator('#statTotal')).to_have_text('24')
                expect(page.locator('#statRejected')).to_have_text('8')
            assert page.evaluate('() => document.body.scrollWidth <= innerWidth')
    page.locator('#resetData').click()
    expect(page.locator('#resetDialog')).to_be_visible()
    page.locator('#resetDialog button[value="cancel"]').click()
    expect(page.locator('#statRejected')).to_have_text('8')
    page.locator('#resetData').click()
    page.locator('#confirmReset').click()
    expect(page.locator('#statRejected')).to_have_text('0')
    assert not errors, errors
    page.locator('#adminSignout').click()
    expect(page.locator('#loginPage')).to_be_visible()
    expect(page.locator('#portal')).to_be_hidden()
    print('Browser smoke passed: all 28 QR images decode exactly, both sample collections, delayed-response collection switching, six viewport widths, reset confirmation, statistics, sign out; no JavaScript errors.')
    browser.close()
