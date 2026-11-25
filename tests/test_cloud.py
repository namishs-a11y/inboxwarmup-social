import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import app
import cloud_run
from report_builder import build

class CloudTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db=patch.object(app,'DB',Path(self.tmp.name)/'posts.sqlite3')
        self.reports=patch.object(app,'REPORTS',Path(self.tmp.name)/'reports')
        self.db.start();self.reports.start()
        self.addCleanup(self.db.stop);self.addCleanup(self.reports.stop)
        app.REPORTS.mkdir()
        self.env=patch.dict(os.environ,{'GROQ_API_KEY':'test','POSTPONE_API_KEY':'test','BUFFER_API_KEY':'test','AUTO_QUEUE_SOCIAL':'true'})
        self.env.start();self.addCleanup(self.env.stop)
    def test_daily_generates_and_queues_once(self):
        def gen(platform,topic,**kwargs):return ('Title '+platform,'Content '+platform)
        with patch.object(app,'generate_custom',side_effect=gen), patch.object(app,'destination',side_effect=lambda p:'test_'+p), patch.object(app,'postpone_schedule',return_value='postpone-id'),patch.object(app,'buffer_x_queue',return_value='buffer-id'):
            self.assertEqual(cloud_run.main(),0)
            self.assertEqual(cloud_run.main(),0)
        c=app.db(); rows=list(c.execute('SELECT platform,status,remote_id FROM posts ORDER BY platform'));c.close()
        self.assertEqual(len(rows),3)
        self.assertEqual({x['platform']:x['status'] for x in rows},{'linkedin':'queued','reddit':'review','x':'queued'})
    def test_report_contains_no_fabricated_metrics(self):
        with patch.object(app,'generate_custom',side_effect=lambda p,t,**kw:('Title','Body')), patch.object(app,'destination',side_effect=lambda p:'account'), patch.object(app,'postpone_schedule',return_value='pp-test'), patch.object(app,'buffer_x_queue',return_value='buffer-test'):
            self.assertEqual(cloud_run.main(),0)
        result=build(app.DB,app.REPORTS)
        self.assertEqual(result['posts'],3)
        self.assertTrue((app.REPORTS/'dashboard.html').exists())
        self.assertIn('Queued is NOT confirmed published', (app.REPORTS/'dashboard.html').read_text())

    def test_failure_is_not_misreported_as_success(self):
        with patch.object(app,'generate_custom',side_effect=lambda p,t,**kw:('Title','Body')),patch.object(app,'destination',side_effect=RuntimeError('Mock provider failure')):
            self.assertEqual(cloud_run.main(),1)
        c=app.db();statuses={x['platform']:x['status'] for x in c.execute('SELECT platform,status FROM posts')};c.close()
        self.assertEqual(statuses['reddit'],'review')
        self.assertEqual(statuses['linkedin'],'verify')
        self.assertEqual(statuses['x'],'verify')

if __name__=='__main__':unittest.main()
