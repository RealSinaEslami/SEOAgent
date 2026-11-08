from database.data import load_data

number = load_data.number
title = load_data.title

def make():
    return(f"/article-{title}-NO-{number}")