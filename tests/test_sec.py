from __future__ import annotations

import tempfile
import unittest
import zipfile
from pathlib import Path

from vcbrain.sources.sec import parse_form_d_zip


class SecFormDTests(unittest.TestCase):
    def test_amount_sold_is_distinct_from_target(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "formd.zip"
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr(
                    "FORMDSUBMISSION.tsv",
                    "ACCESSIONNUMBER\tFILING_DATE\tSUBMISSIONTYPE\n0001\t2025-04-10\tD\n",
                )
                archive.writestr(
                    "ISSUERS.tsv",
                    "ACCESSIONNUMBER\tENTITYNAME\tCIK\n0001\tAcme AI, Inc.\t1234567\n",
                )
                archive.writestr(
                    "OFFERING.tsv",
                    "ACCESSIONNUMBER\tDATEOFFIRSTSALE\tTOTALOFFERINGAMOUNT\tTOTALAMOUNTSOLD\n"
                    "0001\t2025-04-01\t10000000\t3500000\n",
                )
            [record] = parse_form_d_zip(path)
            self.assertEqual(record.first_sale_date, "2025-04-01")
            self.assertEqual(record.amount_sold, 3_500_000)
            self.assertEqual(record.offering_target, 10_000_000)


if __name__ == "__main__":
    unittest.main()

