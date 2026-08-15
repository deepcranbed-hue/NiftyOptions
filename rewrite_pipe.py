content = open("backend/quant/pipeline.py").read()
content = content.replace("from .decision_engine import index_bias, sector_weights", "from .decision_engine import index_bias\nfrom .sector_map import sector_weights")
with open("backend/quant/pipeline.py", "w") as f:
    f.write(content)
