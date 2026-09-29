"""Test de l'extraction d'un bulletin de paie. Temporaire : a supprimer apres validation."""
import sys

from app.services.document_reader import read_document
from app.services.extraction import extract_payslip

if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("Usage : python try_extraction_paie.py \"chemin\\fichier.pdf_ou_image\"")
    pages = read_document(sys.argv[1])
    result = extract_payslip(pages)
    print(result.model_dump_json(indent=2))