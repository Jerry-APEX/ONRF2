import os

bind = '0.0.0.0:' + os.environ.get('PORT', '10000')
workers = 2
threads = 4
timeout = 45
accesslog = '-'
errorlog = '-'
