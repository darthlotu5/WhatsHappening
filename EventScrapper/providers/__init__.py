def get_adapter(source_id):
    from EventScrapper.providers.local_platforms import ADAPTERS as local
    from EventScrapper.providers.ticketing import ADAPTERS as ticketing
    from EventScrapper.providers.directories import ADAPTERS as directories

    return {**local, **ticketing, **directories}[source_id]