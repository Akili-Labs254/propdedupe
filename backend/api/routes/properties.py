"""Property listing endpoints — the core consumer-facing API."""
from typing import Optional, List
from fastapi import APIRouter, Query
from backend.db.client import get_client
from fastapi import APIRouter, Query, HTTPException

router = APIRouter(prefix="/properties", tags=["Properties"])


@router.get("")
def list_properties(
    page: int = Query(1, ge=1, description="Page number"),
    per_page: int = Query(20, ge=1, le=100, description="Results per page"),
    min_price: Optional[float] = Query(None, description="Minimum price (KSh)"),
    max_price: Optional[float] = Query(None, description="Maximum price (KSh)"),
    bedrooms: Optional[int] = Query(None, description="Exact bedroom count"),
    location: Optional[str] = Query(None, description="Location substring match"),
):
    """List deduplicated properties with optional filters and pagination."""
    db = get_client()

    # Start building the query
    query = db.table("properties").select("*", count="exact")

    # Apply filters only if provided
    if min_price is not None:
        query = query.gte("price", min_price)
    if max_price is not None:
        query = query.lte("price", max_price)
    if bedrooms is not None:
        query = query.eq("bedrooms", bedrooms)
    if location is not None:
        query = query.ilike("location", f"%{location}%")

    # Pagination — offset-based
    offset = (page - 1) * per_page
    query = query.order("id").range(offset, offset + per_page - 1)

    result = query.execute()

    return {
        "page": page,
        "per_page": per_page,
        "total": result.count,
        "data": result.data,
    }


@router.get("/{property_id}")
def get_property(property_id: int):
    """Get a single property with all its linked listings."""
    db = get_client()

    # Fetch the property
    prop = (db.table("properties")
            .select("*")
            .eq("id", property_id)
            .execute())

    if not prop.data:
        raise HTTPException(status_code=404, detail="Property not found")

    prop_data = prop.data[0]

    # Fetch all listings linked to this property
    listings = (db.table("listings")
                .select("id, source_id, source_listing_id, url, title, price, "
                        "location, bedrooms, baths, size_sqm, image_url, address")
                .eq("property_id", property_id)
                .order("source_id")
                .execute())

    return {
        "property": prop_data,
        "listings": listings.data,
    }