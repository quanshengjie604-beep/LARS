"""Data-source adapters. Each returns (payload, document_date, source_uri)-style
structured data and never raises fatally — network failures degrade to null so
one flaky source cannot sink a whole company's record."""
